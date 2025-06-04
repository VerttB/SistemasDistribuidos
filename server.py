import threading  # Para locks, se necessário para estruturas de dados compartilhadas
import time
# Para gerenciar os assinantes de cada grupo
from collections import defaultdict
from concurrent import futures

import grpc
from google.protobuf import empty_pb2  # Para o SendMessage

# Importações dos arquivos gerados pelo gRPC e da nossa classe VectorClockManager
import chat_pb2
import chat_pb2_grpc
from src.vector_clock_manager import VectorClockManager

# Constantes do Servidor
SERVER_ID_PROCESS = 2 # Conforme definimos, o servidor é o processo 2
TOTAL_PROCESSES = 3   # ClienteA, ClienteB, Servidor
SERVER_ADDRESS = 'localhost:50051'


class ChatServiceServicer(chat_pb2_grpc.ChatServiceServicer):
    def __init__(self):
        """
        Inicializa o Serviço de Chat do lado do Servidor.

        Aqui criamos o gerenciador de relógio vetorial do servidor,
        que é responsável por incrementar o contador do servidor
        e comparar/mergir relógios recebidos de outros processos.

        Além disso, criamos uma estrutura de dados para armazenar
        os assinantes (streams de resposta) de cada grupo.
        Cada grupo terá uma lista de "objetos assinantes",
        que conterão a stream de resposta para enviar mensagens
        e talvez o ID do processo cliente para depuração ou lógica avançada.
        """
        
        # 1. Gerenciador de Relógio Vetorial do Servidor
        self.vcm = VectorClockManager(process_id=SERVER_ID_PROCESS, num_processes=TOTAL_PROCESSES)
        print(f"Servidor (Processo {SERVER_ID_PROCESS}) iniciado. Relógio: {self.vcm.get_clock_list()}")

        # 2. Estrutura para armazenar os assinantes (streams de resposta) por grupo
        #    A chave será o group_id (string)
        #    O valor será uma lista de "objetos assinantes".
        #    Cada objeto assinante precisará no mínimo da stream de resposta para enviar mensagens
        #    e talvez o ID do processo cliente para depuração ou lógica avançada.
        #    Formato do objeto assinante: {'stream': stream_de_resposta, 'client_process_id': int, 'user_id': str}
        self.groups = defaultdict(list)
        self.groups_lock = threading.Lock() # Para proteger o acesso concorrente a self.groups

        print("Serviço de Chat inicializado.")


    
    def ListGroups(self, request, context):
        group_ids = list(self.groups.keys())
        return chat_pb2.ListGroupsResponse(group_ids=group_ids)
        
    
    def CreateGroup(self, request, context):
        group_id = request.group_id
        if group_id in self.groups:
            print(f"[!] Grupo '{group_id}' já existe.")
        else:
            self.groups[group_id] = []
            print(f"[+] Grupo '{group_id}' criado com sucesso.")
        return empty_pb2.Empty()
        
    def SendMessage(self, request: chat_pb2.ChatMessage, context):
        """
        Handles incoming chat messages from clients and retransmits them to subscribers of a group.

        This method performs the following actions:
        1. Updates the server's vector clock using the vector clock received with the message.
        2. Retransmits the original message along with the original vector clock to all subscribers of the specified group.

        Args:
            request (chat_pb2.ChatMessage): The chat message containing user_id, text, group_id,
                                            and the sender's vector clock.
            context: The gRPC context for the request, used for sending status codes and details.

        Returns:
            google.protobuf.Empty: A confirmation that the message was processed and retransmitted.
        """
        group_id = request.group_id
        sender_user_id = request.user_id
        text = request.text
        received_vc_list = list(request.vector_clock.clock) # Lista de inteiros do relógio do remetente

        print(f"\n[Servidor] Mensagem recebida de '{sender_user_id}' para o grupo '{group_id}': \"{text}\"")
        print(f"[Servidor] Relógio Vetorial Recebido: {received_vc_list}")

        # 1. Servidor atualiza seu próprio Relógio Vetorial com base na mensagem recebida
        self.vcm.update(received_vc_list) # update já chama increment internamente
        print(f"[Servidor] Relógio Vetorial do Servidor Atualizado: {self.vcm.get_clock_list()}")

        # 2. Retransmitir a mensagem para todos os outros clientes no mesmo grupo.
        #    A mensagem retransmitida é a ORIGINAL, com o relógio vetorial ORIGINAL do remetente.
        #    Cada cliente receptor fará seu próprio update ao receber.
        with self.groups_lock:
            if group_id in self.groups:
                subscribers = list(self.groups[group_id]) # Copia a lista para iterar com segurança
                print(f"[Servidor] Retransmitindo para {len(subscribers)} assinante(s) do grupo '{group_id}'...")
                for subscriber in subscribers:
                    try:
                        # Coloca a mensagem na fila individual daquele cliente
                        subscriber['queue'].append(request) # <<< MUDANÇA AQUI
                        print(f"  -> Mensagem ENFILEIRADA para cliente (ID processo: {subscriber.get('client_process_id', 'N/A')}, User: {subscriber.get('user_id', 'N/A')})")
                    except Exception as e: # Captura exceção genérica se a estrutura do assinante estiver quebrada
                        print(f"  [Servidor ERRO] Falha ao ENFILEIRAR mensagem para um assinante do grupo '{group_id}': {e}.")
            else:
                print(f"[Servidor] Nenhum assinante no grupo '{group_id}' para retransmitir.")

        return empty_pb2.Empty() # Retorna uma confirmação vazia


    def SubscribeToGroup(self, request: chat_pb2.SubscriptionRequest, context):
        """
        SubscribeToGroup é um método do servidor que permite que um cliente se inscreva em um grupo
        e receba mensagens enviadas por outros clientes do mesmo grupo.

        O método SubscribeToGroup é um gerador (utiliza yield) que retorna mensagens recebidas
        de outros clientes do grupo. A lógica de streaming para este método é um pouco mais complexa,
        pois o servidor precisa manter uma fila por assinante e enviar mensagens dela quando elas
        forem recebidas.

        No momento, o servidor apenas simula que o cliente fica "pendurado" e o SendMessage se
        encarrega de enviar. A stream de resposta do SubscribeToGroup é gerenciada pelo gRPC.

        A forma correta de implementar SubscribeToGroup é criar uma maneira para que este método
        `yield` mensagens quando elas chegarem.
        """
        group_id = request.group_id
        user_id = request.user_id
        client_process_id = request.client_process_id # ID do processo cliente (0 ou 1)

        print(f"\n[Servidor] Cliente '{user_id}' (Processo {client_process_id}) solicitou inscrição no grupo '{group_id}'")

        # Criamos um objeto para armazenar informações do assinante
        # 'context' aqui é o gRPC ServerUnaryStreamCallContext, usado para a stream de resposta.
        # No caso de uma stream servidor-para-cliente, o 'context' não é diretamente a stream de escrita.
        # A stream é implicitamente gerenciada pelo gRPC quando o método retorna um iterador/generator.
        # Para simplificar, vamos tratar 'context' como a stream para escrita (embora não seja exato,
        # o gRPC lida com isso, e o 'write' funciona no objeto de stream que o gRPC nos dá).
        #
        # Na verdade, o método SubscribeToGroup deve ser um gerador (yield).
        # Vamos ajustar isso. O servidor mantém uma fila ou um mecanismo de notificação.

        # --- Esta parte precisa de uma revisão mais cuidadosa para streaming ---
        # A lógica abaixo para adicionar à self.groups está conceitualmente correta,
        # mas a forma como a stream é gerenciada para um RPC server-streaming é diferente.
        # O método em si precisa *ser* um gerador.

        # Atualização: Em vez de passar o 'context' diretamente, vamos criar uma fila por assinante
        # ou usar um padrão de pub/sub mais robusto.
        # Por agora, vamos apenas registrar a intenção e preparar para o streaming.

        # Adiciona o cliente (stream) à lista de assinantes do grupo
        # A "stream" aqui é o próprio objeto 'context' que pode ser usado para enviar mensagens de volta
        # ou, mais corretamente, o objeto retornado pelo método que o gRPC usa para enviar.
        # Para um server-streaming RPC, o método deve 'yield' mensagens.

        # --- Início da Lógica de Streaming para SubscribeToGroup ---
        # (Esta é uma implementação mais correta para server-streaming)

        # Guardamos a informação do assinante. O 'context' pode ser usado para verificar se o cliente desconectou.
        # A "stream de escrita" real é o que este método vai 'yield'.
        # Vamos precisar de uma forma de passar as mensagens para este 'yield'.
        # Uma maneira é ter uma fila por assinante.

        # Simplificação para o momento:
        # Vamos apenas simular que o cliente fica "pendurado" e o SendMessage se encarrega de enviar.
        # A stream de resposta do SubscribeToGroup será gerenciada pelo gRPC.
        # No SendMessage, vamos iterar sobre essas streams e enviar.

        # O `context` em um método de streaming do servidor não é diretamente a stream de escrita.
        # O que fazemos é que este método *retorna* (ou `yield`s) as mensagens.

        # A forma correta de implementar SubscribeToGroup é criar uma maneira para que
        # este método `yield` mensagens quando elas chegarem.

        # Um objeto para representar o assinante e sua capacidade de receber mensagens
        # Vamos usar uma lista simples como uma "fila" por assinante, e o loop abaixo irá consumir dela.
        # Em uma implementação mais robusta, usaríamos `queue.Queue` ou `asyncio.Queue`.
        message_queue_for_this_client = [] # Não é o ideal para concorrência real

        subscriber_info = {
            'stream': context, # Usaremos context para checar se está ativo. NÃO É A STREAM DE ESCRITA DIRETA.
            'user_id': user_id,
            'client_process_id': client_process_id,
            'queue': message_queue_for_this_client, # Cada cliente terá sua fila
            'active_context': context # Para checar se o cliente ainda está conectado
        }

        with self.groups_lock:
            self.groups[group_id].append(subscriber_info)
            print(f"[Servidor] Cliente '{user_id}' (Proc {client_process_id}) adicionado aos assinantes do grupo '{group_id}'. Total: {len(self.groups[group_id])}")

        # Manter a conexão aberta e enviar mensagens quando disponíveis na fila
        try:
            while context.is_active(): # Checa se o cliente ainda está conectado
                if message_queue_for_this_client:
                    message_to_send = message_queue_for_this_client.pop(0) # Pega a primeira mensagem da fila
                    yield message_to_send # Envia para o cliente
                else:
                    # Se não há mensagens, esperamos um pouco para não consumir CPU excessivamente
                    # Em um sistema real, usaríamos eventos ou condições para esperar eficientemente
                    time.sleep(0.1)
        except grpc.RpcError as e:
            print(f"[Servidor ERRO] Conexão com cliente '{user_id}' (Proc {client_process_id}) no grupo '{group_id}' encerrada: {e}")
        finally:
            # Remover o assinante quando ele desconectar
            with self.groups_lock:
                # Precisamos encontrar e remover o subscriber_info correto da lista
                # É importante fazer isso com cuidado para não causar problemas de concorrência
                # ou remover o assinante errado se vários tiverem informações semelhantes (improvável com 'context')
                for sub_list in self.groups.values():
                    if subscriber_info in sub_list:
                        sub_list.remove(subscriber_info)
                        print(f"[Servidor] Cliente '{user_id}' (Proc {client_process_id}) removido dos assinantes do grupo '{group_id}'.")
                        break
            print(f"[Servidor] Cliente '{user_id}' (Proc {client_process_id}) desconectado do grupo '{group_id}'.")

        # Modificação para SendMessage para usar a fila:
        # No método SendMessage, em vez de subscriber['stream'].write(request), faremos:
        # subscriber['queue'].append(request)


def serve():
    """
    Inicia o servidor gRPC e mantém ele rodando.

    Este método:
    1. Cria um pool de threads para lidar com as requisições do servidor.
    2. Adiciona o serviço `ChatServiceServicer` ao servidor gRPC.
    3. Inicia o servidor na porta especificada (`SERVER_ADDRESS`).
    4. Mantém o thread principal vivo enquanto o servidor está rodando.
    5. Encerra o servidor graciosamente quando o programa é interrompido (CTRL+C).

    Nenhuma entrada ou saída é esperada.
    """
    # Cria um pool de threads para lidar com as requisições do servidor
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    # Adiciona nosso serviço ao servidor gRPC
    chat_pb2_grpc.add_ChatServiceServicer_to_server(ChatServiceServicer(), server)

    # Inicia o servidor na porta especificada
    print(f"Servidor gRPC escutando em {SERVER_ADDRESS}...")
    server.add_insecure_port(SERVER_ADDRESS) # Escuta sem criptografia por simplicidade
    server.start()
    print("Servidor iniciado. Pressione CTRL+C para sair.")

    try:
        # Mantém o thread principal vivo enquanto o servidor está rodando
        # (caso contrário, o programa terminaria e o servidor pararia)
        while True:
            time.sleep(86400) # Dorme por um dia (ou qualquer tempo longo)
    except KeyboardInterrupt:
        print("Servidor encerrando...")
        server.stop(0) # Encerra o servidor graciosamente

if __name__ == '__main__':
    serve()
