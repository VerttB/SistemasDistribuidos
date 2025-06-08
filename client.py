import threading
import time
import grpc

import chat_pb2
import chat_pb2_grpc
from src.vector_clock_manager import VectorClockManager

SERVER_ADDRESS = 'localhost:50051'


class ChatClient:
    def __init__(self, user_id: str, client_process_id: int, group_id: str = None):
        self.user_id = user_id
        self.client_process_id = client_process_id
        self.group_id = group_id

        self.vcm = VectorClockManager(process_id=self.client_process_id, num_processes=1)
        print(f"Cliente '{self.user_id}' (Processo {self.client_process_id}) iniciado. Relógio: {self.vcm.get_clock_list()}")

        self.channel = grpc.insecure_channel(SERVER_ADDRESS)
        self.stub = chat_pb2_grpc.ChatServiceStub(self.channel)
        print(f"Cliente '{self.user_id}' conectado ao servidor em {SERVER_ADDRESS}")

        self.message_history = []
        self.lock = threading.Lock()
        self.listening_thread = None
        self.listening = False

        self.stream_responses = None  # Guarda o iterador do stream para cancelar depois

    def create_group(self, group_id: str, password: str = ""):
        try:
            response = self.stub.CreateGroup(chat_pb2.CreateGroupRequest(group_id=group_id, password=password))
            if response.success:
                print(f"[{self.user_id}] Grupo '{group_id}' criado com sucesso!")
            else:
                print(f"[{self.user_id}] Falha ao criar grupo: {response.message}")
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

    def add_message_to_history(self, message):
        with self.lock:
            self.message_history.append(message)

    def expand_vector_clock(self, new_size: int):
        with self.lock:
            current_size = len(self.vcm.clock)
            if new_size > current_size:
                diff = new_size - current_size
                self.vcm.clock.extend([0] * diff)
                self.vcm.num_processes = new_size
                print(f"[{self.user_id}] Relógio vetorial expandido para {new_size} posições.")

    def update_vector_clock(self, received_clock_list: list[int]):
        with self.lock:
            local_size = len(self.vcm.clock)
            remote_size = len(received_clock_list)
            if remote_size > local_size:
                self.expand_vector_clock(remote_size)

            for i in range(remote_size):
                self.vcm.clock[i] = max(self.vcm.clock[i], received_clock_list[i])
            self.vcm.increment()  # Incrementa após mesclar
            print(f"[{self.user_id}] Relógio vetorial atualizado: {self.vcm.get_clock_list()}")

    def receive_messages(self):
        if not self.group_id:
            print(f"[{self.user_id}] Nenhum grupo selecionado para escutar mensagens. Use '/entrar <nome_do_grupo> [senha]'.")
            return

        print(f"\n[{self.user_id}] Escutando por mensagens no grupo '{self.group_id}'...")
        try:
            subscription_request = chat_pb2.SubscriptionRequest(
                user_id=self.user_id,
                client_process_id=self.client_process_id,
                group_id=self.group_id
            )
            self.stream_responses = self.stub.SubscribeToGroup(subscription_request)  # salvar o iterador

            for message in self.stream_responses:
                # Se flag listening foi desativada, sai do loop
                if not self.listening:
                    print(f"[{self.user_id}] Parando escuta conforme flag.")
                    break

                sender = message.user_id
                text = message.text
                group = message.group_id
                received_vc_list = list(message.vector_clock.clock)

                print(f"\n--- Mensagem Recebida [{self.user_id}] ---")
                print(f"  De: {sender} | Grupo: {group}")
                print(f"  Texto: \"{text}\"")
                print(f"  Relógio Vetorial Recebido: {received_vc_list}")

                self.update_vector_clock(received_vc_list)

                self.add_message_to_history({
                    'user_id': sender,
                    'text': text,
                    'vector_clock': received_vc_list,
                    'group_id': group
                })

                print(f"--- Fim da Mensagem --- (Digite sua mensagem ou 'sair'): ", end='', flush=True)

        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.CANCELLED:
                print(f"[{self.user_id}] Stream cancelado pelo cliente.")
            else:
                print(f"[{self.user_id}] ERRO ao receber mensagens: {e.code()} - {e.details()}")
        except Exception as e:
            print(f"[{self.user_id}] Erro inesperado na thread de recebimento: {e}")
        finally:
            print(f"[{self.user_id}] Thread de recebimento de mensagens encerrada.")
            self.listening = False
            self.stream_responses = None  # Limpa o iterador

    def stop_listening(self):
        self.listening = False
        if self.stream_responses is not None:
            self.stream_responses.cancel()  # cancela o stream gRPC no cliente
            self.stream_responses = None
        if self.listening_thread and self.listening_thread.is_alive():
            self.listening_thread.join(timeout=2)
        print(f"[{self.user_id}] Escuta parada e thread encerrada.")

    def send_message(self, text: str):
        with self.lock:
            self.vcm.increment()
            current_vc_proto = self.vcm.get_clock_proto()

        try:
            print(f"\n[{self.user_id}] Enviando mensagem...")
            print(f"  Relógio Vetorial Cliente '{self.user_id}' para envio: {list(current_vc_proto.clock)}")

            chat_message = chat_pb2.ChatMessage(
                user_id=self.user_id,
                text=text,
                vector_clock=current_vc_proto,
                group_id=self.group_id
            )

            self.stub.SendMessage(chat_message)
            print(f"  Mensagem \"{text}\" enviada para o grupo '{self.group_id}'.")

            self.add_message_to_history({
                'user_id': self.user_id,
                'text': text,
                'vector_clock': list(current_vc_proto.clock),
                'group_id': self.group_id
            })

        except grpc.RpcError as e:
            print(f"[{self.user_id}] ERRO ao enviar mensagem: {e.code()} - {e.details()}")
        except Exception as e:
            print(f"[{self.user_id}] Erro inesperado ao enviar mensagem: {e}")

    def enter_group(self, group_id: str, password: str = ""):
        try:
            response = self.stub.EnterGroup(chat_pb2.EnterGroupRequest(
                group_id=group_id,
                password=password,
                user_id=self.user_id
            ))
            if response.success:
                print(f"[{self.user_id}] Entrou no grupo '{group_id}' com sucesso.")
                self.group_id = group_id

                if self.listening:
                    print(f"[{self.user_id}] Já está escutando mensagens de um grupo. Reiniciando thread de escuta...")
                    self.stop_listening()  # usa o método para cancelar o stream e esperar a thread encerrar

                self.listening = True
                self.listening_thread = threading.Thread(target=self.receive_messages, daemon=True)
                self.listening_thread.start()
            else:
                print(f"[{self.user_id}] Falha ao entrar no grupo: {response.message}")
        except grpc.RpcError as e:
            print(f"[{self.user_id}] ERRO ao entrar no grupo: {e.code()} - {e.details()}")
        except Exception as e:
            print(f"[{self.user_id}] Erro inesperado ao entrar no grupo: {e}")

    def leave_group(self):
        if not self.group_id:
            print(f"[{self.user_id}] Você não está em nenhum grupo para sair.")
            return
        try:
            response = self.stub.LeaveGroup(chat_pb2.LeaveGroupRequest(
                group_id=self.group_id,
                user_id=self.user_id
            ))
            if response.success:
                print(f"[{self.user_id}] Saiu do grupo '{self.group_id}' com sucesso.")
                self.group_id = None
                self.stop_listening()  # garante parar a thread e o stream
            else:
                print(f"[{self.user_id}] Falha ao sair do grupo: {response.message}")
        except grpc.RpcError as e:
            print(f"[{self.user_id}] ERRO ao sair do grupo: {e.code()} - {e.details()}")
        except Exception as e:
            print(f"[{self.user_id}] Erro inesperado ao sair do grupo: {e}")

    def start_chat(self):
        print(f"Cliente '{self.user_id}' iniciado. Digite 'sair' para encerrar.")
        try:
            while True:
                message_text = input(f"[{self.user_id} VC:{self.vcm.get_clock_list()}] Digite sua mensagem (ou comandos): ")
                msg = message_text.strip()

                if msg.lower() == 'sair':
                    print(f"[{self.user_id}] Encerrando chat...")
                    break
                elif msg.startswith('/criar '):
                    parts = msg.split(maxsplit=2)
                    group = parts[1] if len(parts) > 1 else ""
                    password = parts[2] if len(parts) > 2 else ""
                    if group:
                        self.create_group(group, password)
                    else:
                        print("Uso correto: /criar <nome_do_grupo> [senha]")
                elif msg.startswith('/entrar '):
                    parts = msg.split(maxsplit=2)
                    if len(parts) >= 2:
                        novo_grupo = parts[1]
                        senha = parts[2] if len(parts) == 3 else ""
                        self.enter_group(novo_grupo, senha)
                    else:
                        print("Uso correto: /entrar <nome_do_grupo> [senha]")
                elif msg == '/listagrupos':
                    self.list_groups()
                elif msg == '/sairgrupo':
                    self.leave_group()
                elif msg == '':
                    continue
                else:
                    if not self.group_id:
                        print("Você precisa entrar em um grupo primeiro (/entrar <nome_do_grupo>) para enviar mensagens.")
                        continue
                    self.send_message(msg)
        except KeyboardInterrupt:
            print(f"\n[{self.user_id}] Chat encerrado pelo usuário.")
            self.stop_listening()  # garante fechar tudo ao interromper o cliente


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Uso: python chat_client.py <user_id> <client_process_id>")
        sys.exit(1)

    user_id = sys.argv[1]
    client_process_id = int(sys.argv[2])
    client = ChatClient(user_id=user_id, client_process_id=client_process_id)
    client.start_chat()
