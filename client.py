import threading
import time
import random
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
        
    def create_group(self, group_id: str):
        try:
            response = self.stub.CreateGroup(chat_pb2.CreateGroupRequest(group_id=group_id))
            if response.success:
                print(f"[{self.user_id}] Grupo '{group_id}' criado com sucesso!")
            else:
                print(f"[{self.user_id}] Falha ao criar grupo '{group_id}'.")
        except grpc.RpcError as e:
            print(f"[{self.user_id}] ERRO ao criar grupo: {e.code()} - {e.details()}")
        except Exception as e:
            print(f"[{self.user_id}] Erro inesperado ao criar grupo: {e}")

    def list_groups(self):
        try:
            response = self.stub.ListGroups(chat_pb2.ListGroupsRequest())
            if response.group_ids:
                print(f"[{self.user_id}] Grupos disponíveis:")
                for gid in response.group_ids:
                    print(f" - {gid}")
            else:
                print(f"[{self.user_id}] Nenhum grupo disponível no momento.")
        except grpc.RpcError as e:
            print(f"[{self.user_id}] ERRO ao listar grupos: {e.code()} - {e.details()}")
        except Exception as e:
            print(f"[{self.user_id}] Erro inesperado ao listar grupos: {e}")


    def receive_messages(self):
        """
        Este método será executado em uma thread separada para escutar continuamente
        por mensagens do servidor para o grupo subscrito.
        """
        if not self.group_id:
            print(f"[{self.user_id}] Nenhum grupo selecionado para escutar mensagens. Use '/entrar <nome_do_grupo>' para entrar em um grupo ou /criar <nome_do_grupo> para criar um grupo.")
            return

        print(f"\n[{self.user_id}] Escutando por mensagens no grupo '{self.group_id}'...")
        try:
            subscription_request = chat_pb2.SubscriptionRequest(
                user_id=self.user_id,
                client_process_id=self.client_process_id,
                group_id=self.group_id
            )
            stream_responses = self.stub.SubscribeToGroup(subscription_request)

            for message in stream_responses:
                sender = message.user_id
                text = message.text
                group = message.group_id
                received_vc_list = list(message.vector_clock.clock)

                print(f"\n--- Mensagem Recebida [{self.user_id}] ---")
                print(f"  De: {sender} | Grupo: {group}")
                print(f"  Texto: \"{text}\"")
                print(f"  Relógio Vetorial Recebido: {received_vc_list}")

                self.vcm.update(received_vc_list)
                print(f"  Relógio Vetorial Cliente '{self.user_id}' Atualizado: {self.vcm.get_clock_list()}")
                print(f"--- Fim da Mensagem --- (Digite sua mensagem ou 'sair'): ", end='', flush=True)

        except grpc.RpcError as e:
            print(f"[{self.user_id}] ERRO ao receber mensagens: {e.code()} - {e.details()}")
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
                 message_text = input(f"[{self.user_id} VC:{self.vcm.get_clock_list()}] Digite sua mensagem (ou 'sair'): ")
                 msg = message_text.strip().lower()
                 if msg == 'sair':
                    print(f"[{self.user_id}] Encerrando chat...")
                    break
                 elif msg.startswith('/criar '):
                    novo_grupo = message_text[7:].strip()
                    if novo_grupo:
                        self.create_group(novo_grupo)
                        self.group_id = novo_grupo
                        print(f"[{self.user_id}] Entrando no grupo '{novo_grupo}'...")
                    else:
                        print("Uso correto: /criar <nome_do_grupo>")
                 elif msg == '/grupos':
                    self.list_groups()
                 elif msg.startswith('/entrar '):
                    novo_grupo = message_text[7:].strip()
                    if novo_grupo:
                        self.group_id = novo_grupo
                        print(f"[{self.user_id}] Entrando no grupo '{novo_grupo}'...")
                        # Inicia thread para escutar o novo grupo
                        threading.Thread(target=self.receive_messages, daemon=True).start()
                    else:
                        print("Uso correto: /entrar <nome_do_grupo>")
                 elif msg:
                    if not self.group_id:
                        print(f"[{self.user_id}] Você precisa entrar em um grupo antes de enviar mensagens. Use '/entrar <nome_do_grupo>'.")
                    else:
                        self.send_message(message_text)
        except KeyboardInterrupt:
            print(f"\n[{self.user_id}] Encerrando chat por interrupção...")
        finally:
            if self.channel:
                self.channel.close()
            print(f"[{self.user_id}] Chat encerrado.")


if __name__ == '__main__':
    try:
        USER_ID = input("Digite seu nome de usuário: ").strip()
        CLIENT_PROCESS_ID = random.randint(0, 1000)



        client = ChatClient(user_id=USER_ID, client_process_id=CLIENT_PROCESS_ID, group_id=None)
        client.start_chat()

    except ValueError:
        print("Entrada inválida para ID de Processo. Por favor, digite um número (0 ou 1).")
    except Exception as e:
        print(f"Erro ao iniciar o cliente: {e}")