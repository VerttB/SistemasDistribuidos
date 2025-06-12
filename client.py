import threading
import time
import grpc
from concurrent import futures
import socket

import chat_pb2
import chat_pb2_grpc
from src.vector_clock_manager import VectorClockManager

DISCOVERY_SERVER_ADDRESS = 'localhost:50051'
MAX_GROUP_SIZE = 20

def _get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.bind(('', 0)); port = s.getsockname()[1]; s.close(); return port
def _get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try: s.connect(('8.8.8.8', 1)); ip = s.getsockname()[0]
    except Exception: ip = '127.0.0.1'
    finally: s.close()
    return ip

class PeerServicer(chat_pb2_grpc.PeerServiceServicer):
    def __init__(self, client_instance): self.client = client_instance
    def SendDirectMessage(self, request: chat_pb2.ChatMessage, context):
        self.client.receive_direct_message(request)
        return chat_pb2.google_dot_protobuf_dot_empty__pb2.Empty()

class P2PChatClient:
    def __init__(self, user_id: str, peer_address: str):
        self.user_id = user_id; self.peer_address = peer_address
        self.group_id = None; self.process_id = None; self.vcm = None
        self.discovery_channel = grpc.insecure_channel(DISCOVERY_SERVER_ADDRESS)
        self.discovery_stub = chat_pb2_grpc.DiscoveryServiceStub(self.discovery_channel)
        self.peers = {}; self.lock = threading.Lock()
        self.is_listening_to_events = threading.Event()
        
        self.peer_server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        chat_pb2_grpc.add_PeerServiceServicer_to_server(PeerServicer(self), self.peer_server)
        self.peer_server.add_insecure_port(self.peer_address)

    def start_peer_server(self):
        print(f"[{self.user_id}] Iniciando servidor P2P em {self.peer_address}")
        self.peer_server.start()

    def stop_peer_server(self):
        self.is_listening_to_events.set()
        print(f"[{self.user_id}] Parando servidor P2P.")
        self.peer_server.stop(1)

    def receive_direct_message(self, message: chat_pb2.ChatMessage):
        if self.vcm is None: return
        with self.lock: self.vcm.update(list(message.vector_clock.clock))
        # Limpa a linha atual antes de imprimir a mensagem recebida
        print(f"\r{' ' * 80}\r", end='')
        print(f"<{message.user_id}> {message.text}")
        self._print_prompt()

    def _print_prompt(self):
        prompt = f"[{self.user_id}@{self.group_id or 'Lobby'}]"
        if self.process_id is not None: prompt = f"[{self.user_id}:{self.process_id}@{self.group_id}]"
        print(f"{prompt} > ", end='', flush=True)

    def _connect_to_peer(self, peer_info: chat_pb2.PeerInfo):
        if peer_info.user_id == self.user_id or peer_info.user_id in self.peers: return
        print(f"\n[Sistema] Conectando ao peer '{peer_info.user_id}'...")
        self._print_prompt()
        channel = grpc.insecure_channel(peer_info.address)
        self.peers[peer_info.user_id] = chat_pb2_grpc.PeerServiceStub(channel)

    def _disconnect_from_peer(self, user_id: str):
        if user_id in self.peers:
            print(f"\n[Sistema] Peer '{user_id}' saiu.")
            self._print_prompt()
            del self.peers[user_id]

    def _listen_for_discovery_events(self):
        self.is_listening_to_events.clear()
        try:
            req = chat_pb2.SubscriptionRequest(user_id=self.user_id, group_id=self.group_id)
            for event in self.discovery_stub.SubscribeToGroupEvents(req):
                if self.is_listening_to_events.is_set(): break
                with self.lock:
                    if event.HasField("user_joined"): self._connect_to_peer(event.user_joined)
                    elif event.HasField("user_left_id"): self._disconnect_from_peer(event.user_left_id)
        except grpc.RpcError:
            print("\n[Sistema] Conexão com o servidor de descoberta perdida. O chat continua apenas com os peers atuais.")
            self._print_prompt()

    def create_group(self, group_id: str, pw: str = ""):
        try: print(f"[Sistema] {self.discovery_stub.CreateGroup(chat_pb2.CreateGroupRequest(group_id=group_id, password=pw)).message}")
        except grpc.RpcError as e: print(f"[Sistema] ERRO: {e.details()}")

    def list_groups(self):
        try:
            res = self.discovery_stub.ListGroups(chat_pb2.ListGroupsRequest())
            if not res.group_ids: print("[Sistema] Nenhum grupo disponível."); return
            print("[Sistema] Grupos disponíveis:"); [print(f"  - {gid}") for gid in res.group_ids]
        except grpc.RpcError as e: print(f"[Sistema] ERRO: {e.details()}")

    def enter_group(self, group_id: str, pw: str = ""):
        if self.group_id: print("[Sistema] Você já está em um grupo."); return
        try:
            req = chat_pb2.EnterGroupRequest(group_id=group_id, password=pw, user_id=self.user_id, peer_address=self.peer_address)
            res = self.discovery_stub.EnterGroup(req)
            if not res.success: print(f"[Sistema] Falha: {res.message}"); return

            self.group_id = group_id
            self.process_id = res.assigned_process_id
            self.vcm = VectorClockManager(process_id=self.process_id, num_processes=MAX_GROUP_SIZE)
            print(f"[Sistema] Conectado a '{group_id}' com ID {self.process_id}.")

            if res.history:
                print("\n[Grupo]: --- Histórico do Grupo ---")
                for msg in res.history:
                    print(f"<{msg.user_id}> {msg.text}")
                    with self.lock: self.vcm.merge_with_max(list(msg.vector_clock.clock))
                print("[Grupo]: --- Fim do Histórico ---\n")

            with self.lock:
                for peer in res.existing_peers: self._connect_to_peer(peer)
            
            threading.Thread(target=self._listen_for_discovery_events, daemon=True).start()
        except grpc.RpcError as e: print(f"[Sistema] ERRO: Servidor ta offline ou grupo não encontrado.")

    def leave_group(self):
        if not self.group_id: return
        try: self.discovery_stub.LeaveGroup(chat_pb2.LeaveGroupRequest(group_id=self.group_id, user_id=self.user_id))
        except grpc.RpcError: pass 
        finally:
            self.is_listening_to_events.set()
            print(f"[Sistema] Você saiu do grupo '{self.group_id}'.")
            self.group_id = None; self.process_id = None; self.vcm = None; self.peers.clear()

    def send_message(self, text: str):
        with self.lock:
            if self.vcm is None:
                print("[Sistema] Você precisa estar em um grupo para enviar mensagens.")
                return
            self.vcm.increment()
            message = chat_pb2.ChatMessage(user_id=self.user_id, text=text, vector_clock=self.vcm.get_clock_proto(), group_id=self.group_id)
           
            peers_snapshot = list(self.peers.items())

        if not peers_snapshot:
            print("[Sistema] Nenhum outro participante no grupo para enviar mensagem.")
        
        for uid, stub in peers_snapshot:
            try: stub.SendDirectMessage(message, timeout=1)
            except grpc.RpcError: print(f"[Sistema] ERRO: Falha ao enviar para {uid}.")
        
        try: self.discovery_stub.LogMessage(message, timeout=1)
        except grpc.RpcError: pass 

    def show_help(self):
        print("\n--- Comandos ---");
        if self.group_id:
            print("/sairgrupo            - Sai do grupo atual."); print("Qualquer outro texto    - Envia uma mensagem.")
        else:
            print("/criar <grupo> [senha]  - Cria um novo grupo."); print("/listagrupos            - Lista os grupos."); print("/entrar <grupo> [senha] - Entra em um grupo.")
        print("/ajuda                  - Mostra esta ajuda."); print("sair                    - Encerra o cliente."); print("------------------\n")

    def start_chat(self):
        server_thread = threading.Thread(target=self.start_peer_server, daemon=True); server_thread.start()
        time.sleep(1); print("Bem-vindo ao Chat P2P!"); self.show_help()
        try:
            while True:
                self._print_prompt(); cmd = input() # Removido .strip() para evitar erro no Ctrl+C
                if not cmd: continue
                if cmd.lower() == 'sair': break
                elif cmd.lower() == '/ajuda': self.show_help()
                elif self.group_id:
                    if cmd.lower() == '/sairgrupo': self.leave_group()
                    else: self.send_message(cmd)
                else: # Lobby
                    if cmd.startswith('/criar '): parts = cmd.split(maxsplit=2); self.create_group(parts[1], parts[2] if len(parts) > 2 else "")
                    elif cmd.lower() == '/listagrupos': self.list_groups()
                    elif cmd.startswith('/entrar '): parts = cmd.split(maxsplit=2); self.enter_group(parts[1], parts[2] if len(parts) > 2 else "")
                    else: print("[Sistema] Comando inválido no lobby.")
        except (KeyboardInterrupt, EOFError): print("\nEncerrando...")
        finally: self.leave_group(); self.stop_peer_server()

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2: print("Uso: python peer_client.py <user_id>"); sys.exit(1)
    
    client = P2PChatClient(user_id=sys.argv[1], peer_address=f"{_get_local_ip()}:{_get_free_port()}")
    client.start_chat()