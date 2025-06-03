import threading
import time

import grpc

# Importações dos arquivos gerados e do VectorClockManager
import chat_pb2
import chat_pb2_grpc
from src.vector_clock_manager import VectorClockManager

# Configurações do Cliente (podem ser passadas como argumentos de linha de comando no futuro)
SERVER_ADDRESS = 'localhost:50051'
# CLIENT_PROCESS_ID será definido ao rodar (0 para ClienteA, 1 para ClienteB)
# USER_ID será baseado no CLIENT_PROCESS_ID
TOTAL_PROCESSES = 3 # ClienteA, ClienteB, Servidor
DEFAULT_GROUP_ID = "GrupoPrincipal"


class ChatClient:
    def __init__(self, user_id: str, client_process_id: int, group_id: str):
        """
        Initializes a chat client with user identification, process ID, and group subscription.

        Args:
            user_id (str): The unique identifier for the user.
            client_process_id (int): The process ID for the client (0 for ClienteA, 1 for ClienteB).
            group_id (str): The identifier for the group to which the client will subscribe.

        This constructor sets up the vector clock manager for the client, initializes the gRPC
        channel and stub, and connects the client to the server.
        """
        self.user_id = user_id
        self.client_process_id = client_process_id
        self.group_id = group_id

        # 1. Gerenciador de Relógio Vetorial do Cliente
        self.vcm = VectorClockManager(process_id=self.client_process_id, num_processes=TOTAL_PROCESSES)
        print(f"Cliente '{self.user_id}' (Processo {self.client_process_id}) iniciado. Relógio: {self.vcm.get_clock_list()}")

        # 2. Configurar canal gRPC e stub
        self.channel = grpc.insecure_channel(SERVER_ADDRESS)
        self.stub = chat_pb2_grpc.ChatServiceStub(self.channel)
        print(f"Cliente '{self.user_id}' conectado ao servidor em {SERVER_ADDRESS}")

    def receive_messages(self):
        """
        Este método será executado em uma thread separada para escutar continuamente
        por mensagens do servidor para o grupo subscrito.
        """
        print(f"\n[{self.user_id}] Escutando por mensagens no grupo '{self.group_id}'...")
        try:
            # 1. Criar a requisição de subscrição
            subscription_request = chat_pb2.SubscriptionRequest(
                user_id=self.user_id,
                client_process_id=self.client_process_id,
                group_id=self.group_id
            )
            # 2. Chamar o método RPC de streaming do servidor
            stream_responses = self.stub.SubscribeToGroup(subscription_request)

            # 3. Iterar sobre as mensagens recebidas na stream
            for message in stream_responses:
                sender = message.user_id
                text = message.text
                group = message.group_id
                received_vc_list = list(message.vector_clock.clock)

                print(f"\n--- Mensagem Recebida [{self.user_id}] ---")
                print(f"  De: {sender} | Grupo: {group}")
                print(f"  Texto: \"{text}\"")
                print(f"  Relógio Vetorial Recebido: {received_vc_list}")

                # Atualizar o relógio vetorial do cliente com base na mensagem recebida
                self.vcm.update(received_vc_list) # update já chama increment internamente
                print(f"  Relógio Vetorial Cliente '{self.user_id}' Atualizado: {self.vcm.get_clock_list()}")
                print(f"--- Fim da Mensagem --- (Digite sua mensagem ou 'sair'): ", end='', flush=True)


        except grpc.RpcError as e:
            print(f"[{self.user_id}] ERRO ao receber mensagens: {e.code()} - {e.details()}")
            if e.code() == grpc.StatusCode.UNAVAILABLE:
                print(f"[{self.user_id}] O servidor parece estar indisponível. Encerrando.")
            # Outros códigos de erro podem ser tratados aqui
        except Exception as e:
            print(f"[{self.user_id}] Erro inesperado na thread de recebimento: {e}")
        finally:
            print(f"[{self.user_id}] Thread de recebimento de mensagens encerrada.")


    def send_message(self, text: str):
        """
        Envia uma mensagem para o servidor.
        """
        try:
            # 1. Cliente incrementa seu relógio vetorial (evento de envio)
            self.vcm.increment()
            current_vc_proto = self.vcm.get_clock_proto()
            print(f"\n[{self.user_id}] Enviando mensagem...")
            print(f"  Relógio Vetorial Cliente '{self.user_id}' para Envio: {list(current_vc_proto.clock)}")


            # 2. Criar a mensagem de chat
            chat_message = chat_pb2.ChatMessage(
                user_id=self.user_id,
                text=text,
                vector_clock=current_vc_proto,
                group_id=self.group_id
            )

            # 3. Chamar o método RPC SendMessage
            self.stub.SendMessage(chat_message)
            print(f"  Mensagem \"{text}\" enviada para o grupo '{self.group_id}'.")

        except grpc.RpcError as e:
            print(f"[{self.user_id}] ERRO ao enviar mensagem: {e.code()} - {e.details()}")
        except Exception as e:
            print(f"[{self.user_id}] Erro inesperado ao enviar mensagem: {e}")


    def start_chat(self):
        """
        Inicia o chat com o servidor, criando uma thread para escutar por mensagens
        e outra para enviar mensagens. O cliente continuará a funcionar até que o
        usuário digite 'sair' ou pressione Ctrl+C.

        A thread de recebimento de mensagens é executada em segundo plano e
        imprime as mensagens recebidas no console. A thread de envio de mensagens
        é executada na thread principal e lê as mensagens do usuário para enviar
        ao servidor.

        Se o servidor estiver indisponível, o cliente imprimirá um erro e
        encerrará. Se o cliente receber uma mensagem inválida do servidor, ele
        imprimirá um erro e continuará a funcionar normalmente.
        """
        # Iniciar a thread para receber mensagens
        receive_thread = threading.Thread(target=self.receive_messages, daemon=True)
        # daemon=True faz com que a thread seja encerrada quando o programa principal sair
        receive_thread.start()
        print(f"Cliente '{self.user_id}' iniciado. Digite 'sair' para encerrar.")

        try:
            while True:
                # Pequeno delay para o print de "Escutando por mensagens" aparecer antes do input
                if not receive_thread.is_alive() and self.user_id: # Hack para o primeiro prompt
                    time.sleep(0.2)

                message_text = input(f"[{self.user_id} para {self.group_id} VC:{self.vcm.get_clock_list()}] Digite sua mensagem (ou 'sair'): ")
                if message_text.lower() == 'sair':
                    print(f"[{self.user_id}] Encerrando chat...")
                    break
                if message_text: # Só envia se não for vazio
                    self.send_message(message_text)
        except KeyboardInterrupt:
            print(f"\n[{self.user_id}] Encerrando chat por interrupção...")
        finally:
            if self.channel:
                self.channel.close()
            print(f"[{self.user_id}] Chat encerrado.")


if __name__ == '__main__':
    try:
        client_process_id_input = input("Digite o ID do Processo deste Cliente (0 para ClienteA, 1 para ClienteB): ")
        CLIENT_PROCESS_ID = int(client_process_id_input)

        if CLIENT_PROCESS_ID == 0:
            USER_ID = "Alice"
        elif CLIENT_PROCESS_ID == 1:
            USER_ID = "Bob"
        else:
            print("ID de Processo inválido. Use 0 ou 1.")
            exit()

        client = ChatClient(user_id=USER_ID, client_process_id=CLIENT_PROCESS_ID, group_id=DEFAULT_GROUP_ID)
        client.start_chat()

    except ValueError:
        print("Entrada inválida para ID de Processo. Por favor, digite um número (0 ou 1).")
    except Exception as e:
        print(f"Erro ao iniciar o cliente: {e}")