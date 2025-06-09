import threading
import time
import grpc
from concurrent import futures

import chat_pb2
import chat_pb2_grpc
from src.vector_clock_manager import VectorClockManager

DISCOVERY_SERVER_ADDRESS = 'localhost:50051'

# Implementação do serviço que o cliente hospeda para outros peers
class PeerServicer(chat_pb2_grpc.PeerServiceServicer):
    def __init__(self, client_instance):
        self.client = client_instance

    def SendDirectMessage(self, request: chat_pb2.ChatMessage, context):
        # Este método é chamado por OUTRO peer
        self.client.receive_direct_message(request)
        return chat_pb2.google_dot_protobuf_dot_empty__pb2.Empty()

class P2PChatClient:
    def __init__(self, user_id: str, process_id: int, peer_address: str):
        self.user_id = user_id
        self.process_id = process_id
        self.peer_address = peer_address
        self.group_id = None
        
        # O tamanho inicial precisa ser gerenciado dinamicamente ou ser grande o suficiente.
        self.vcm = VectorClockManager(process_id=process_id, num_processes=10)

        # Conexão com o servidor de descoberta
        self.discovery_channel = grpc.insecure_channel(DISCOVERY_SERVER_ADDRESS)
        self.discovery_stub = chat_pb2_grpc.DiscoveryServiceStub(self.discovery_channel)

        # Dicionário para armazenar stubs de outros peers
        self.peers = {} # user_id -> stub
        self.lock = threading.Lock()
        
        # Servidor gRPC que este cliente hospeda
        self.peer_server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        chat_pb2_grpc.add_PeerServiceServicer_to_server(PeerServicer(self), self.peer_server)
        self.peer_server.add_insecure_port(self.peer_address)

    def start_peer_server(self):
        print(f"[{self.user_id}] Iniciando servidor P2P em {self.peer_address}")
        self.peer_server.start()
        # Não bloquear, rodará em uma thread

    def stop_peer_server(self):
        print(f"[{self.user_id}] Parando servidor P2P.")
        self.peer_server.stop(0)

    def receive_direct_message(self, message: chat_pb2.ChatMessage):
        received_vc_list = list(message.vector_clock.clock)
        
        print(f"\n--- Mensagem Recebida de {message.user_id} ---")
        print(f"  Texto: \"{message.text}\"")
        print(f"  Relógio Recebido: {received_vc_list}")
        
        with self.lock:
            self.vcm.update(received_vc_list)
        
        print(f"  Meu Relógio Atualizado: {self.vcm.get_clock_list()}")
        print(f"--- Fim da Mensagem --- (Digite sua mensagem ou /ajuda): ", end='', flush=True)

    def create_group(self, group_id: str, password: str = ""):
        try:
            request = chat_pb2.CreateGroupRequest(group_id=group_id, password=password)
            response = self.discovery_stub.CreateGroup(request)
            print(f"[Sistema] {response.message}")
        except grpc.RpcError as e:
            print(f"[Sistema] ERRO ao criar grupo: {e.details()}")

    def list_groups(self):
        try:
            request = chat_pb2.ListGroupsRequest()
            response = self.discovery_stub.ListGroups(request)
            if not response.group_ids:
                print("[Sistema] Nenhum grupo disponível no momento.")
                return
            
            print("[Sistema] Grupos disponíveis:")
            for gid in response.group_ids:
                print(f"  - {gid}")
        except grpc.RpcError as e:
            print(f"[Sistema] ERRO ao listar grupos: {e.details()}")

    def enter_group(self, group_id: str, password: str = ""):
        try:
            req = chat_pb2.EnterGroupRequest(
                group_id=group_id, password=password, user_id=self.user_id,
                peer_address=self.peer_address, client_process_id=self.process_id
            )
            response = self.discovery_stub.EnterGroup(req)
            if not response.success:
                print(f"[Sistema] Falha ao entrar no grupo: {response.message}")
                return

            self.group_id = group_id
            print(f"[Sistema] Entrou no grupo '{group_id}' com sucesso. Buscando outros peers...")
            
            participants_resp = self.discovery_stub.GetGroupParticipants(
                chat_pb2.GetGroupParticipantsRequest(group_id=self.group_id)
            )
            with self.lock:
                for peer in participants_resp.peers:
                    if peer.user_id != self.user_id:
                        self._connect_to_peer(peer)
            
            threading.Thread(target=self._listen_for_discovery_events, daemon=True).start()

        except grpc.RpcError as e:
            print(f"[Sistema] ERRO ao entrar no grupo: {e.details()}")

    def _connect_to_peer(self, peer_info: chat_pb2.PeerInfo):
        if peer_info.user_id in self.peers:
            return
        print(f"[Sistema] Conectando ao peer '{peer_info.user_id}' em {peer_info.address}...")
        channel = grpc.insecure_channel(peer_info.address)
        stub = chat_pb2_grpc.PeerServiceStub(channel)
        self.peers[peer_info.user_id] = stub

    def _disconnect_from_peer(self, user_id: str):
        if user_id in self.peers:
            print(f"[Sistema] Desconectando do peer '{user_id}'.")
            del self.peers[user_id]

    def _listen_for_discovery_events(self):
        try:
            request = chat_pb2.SubscriptionRequest(user_id=self.user_id, group_id=self.group_id)
            event_stream = self.discovery_stub.SubscribeToGroupEvents(request)
            for event in event_stream:
                with self.lock:
                    if event.HasField("user_joined"):
                        self._connect_to_peer(event.user_joined)
                    elif event.HasField("user_left_id"):
                        self._disconnect_from_peer(event.user_left_id)
        except grpc.RpcError:
            print("[Sistema] Conexão com o servidor de descoberta perdida.")

    def send_message(self, text: str):
        if not self.group_id:
            print("[Sistema] Você precisa entrar em um grupo para enviar mensagens.")
            return

        with self.lock:
            self.vcm.increment()
            message = chat_pb2.ChatMessage(
                user_id=self.user_id, text=text,
                vector_clock=self.vcm.get_clock_proto(),
                group_id=self.group_id
            )
        
        with self.lock:
            if not self.peers:
                print("[Sistema] Nenhum outro participante no grupo para enviar mensagem.")
                return

            print(f"[Sistema] Enviando '{text}' com relógio {self.vcm.get_clock_list()} para {len(self.peers)} peer(s).")
            peers_to_remove = []
            for uid, stub in self.peers.items():
                try:
                    stub.SendDirectMessage(message, timeout=2)
                except grpc.RpcError:
                    print(f"[Sistema] ERRO: Não foi possível enviar mensagem para {uid}. Peer pode estar offline.")
                    peers_to_remove.append(uid)
            
            for uid in peers_to_remove:
                self._disconnect_from_peer(uid)

    def show_help(self):
        print("\n--- Comandos Disponíveis ---")
        print("/ajuda                  - Mostra esta mensagem de ajuda.")
        print("/criar <grupo> [senha]  - Cria um novo grupo.")
        print("/listagrupos            - Lista todos os grupos disponíveis.")
        print("/entrar <grupo> [senha] - Entra em um grupo existente.")
        print("Qualquer outro texto      - Envia uma mensagem para o grupo atual.")
        print("sair                    - Encerra o cliente.")
        print("--------------------------\n")

    def start_chat(self):
        server_thread = threading.Thread(target=self.start_peer_server, daemon=True)
        server_thread.start()
        time.sleep(1)

        print("Bem-vindo ao Chat P2P!")
        self.show_help()
        
        try:
            while True:
                cmd = input(f"[{self.user_id}@{self.group_id or 'Fora de grupo'}] > ")
                
                if cmd.lower() == 'sair':
                    break
                elif cmd.lower() == '/ajuda':
                    self.show_help()
                elif cmd.startswith('/criar '):
                    parts = cmd.split(maxsplit=2)
                    if len(parts) > 1:
                        self.create_group(parts[1], parts[2] if len(parts) > 2 else "")
                    else:
                        print("Uso: /criar <nome_do_grupo> [senha]")
                elif cmd.lower() == '/listagrupos':
                    self.list_groups()
                elif cmd.startswith('/entrar '):
                    parts = cmd.split(maxsplit=2)
                    if len(parts) > 1:
                        self.enter_group(parts[1], parts[2] if len(parts) > 2 else "")
                    else:
                        print("Uso: /entrar <nome_do_grupo> [senha]")
                elif self.group_id:
                    self.send_message(cmd)
                else:
                    print("[Sistema] Comando não reconhecido ou você não está em um grupo. Use /ajuda para ver os comandos.")

        except KeyboardInterrupt:
            print("\nEncerrando...")
        finally:
            if self.group_id:
                try:
                    self.discovery_stub.LeaveGroup(chat_pb2.LeaveGroupRequest(group_id=self.group_id, user_id=self.user_id), timeout=2)
                except grpc.RpcError:
                    print("[Sistema] Não foi possível notificar o servidor da sua saída.")
            self.stop_peer_server()


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 4:
        print("Uso: python peer_client.py <user_id> <process_id> <host:porta_local>")
        print("Exemplo: python peer_client.py alice 0 localhost:50060")
        sys.exit(1)

    user_id = sys.argv[1]
    process_id = int(sys.argv[2])
    peer_address = sys.argv[3]
    
    client = P2PChatClient(user_id, process_id, peer_address)
    client.start_chat()