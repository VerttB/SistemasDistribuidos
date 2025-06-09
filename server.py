import threading
from concurrent import futures
import grpc
import chat_pb2
import chat_pb2_grpc
import queue
import time

class GroupInfo:
    def __init__(self, group_id, password=None):
        self.group_id = group_id
        self.password = password
        self.participants = {}  # Mapeia user_id -> PeerInfo
        self.subscribers = {}   # Mapeia user_id -> fila de eventos (queue.Queue)
        self.lock = threading.Lock()

    def add_participant(self, peer_info: chat_pb2.PeerInfo):
        with self.lock:
            self.participants[peer_info.user_id] = peer_info

    def remove_participant(self, user_id: str):
        with self.lock:
            if user_id in self.participants:
                del self.participants[user_id]
            if user_id in self.subscribers:
                self.subscribers[user_id].put(None)
                del self.subscribers[user_id]

    def add_subscriber(self, user_id: str, event_queue: queue.Queue):
        with self.lock:
            self.subscribers[user_id] = event_queue

    def broadcast_event(self, event: chat_pb2.GroupEvent, exclude_user_id: str = None):
        with self.lock:
            for user_id, q in self.subscribers.items():
                if user_id != exclude_user_id:
                    q.put(event)

class DiscoveryServiceServicer(chat_pb2_grpc.DiscoveryServiceServicer):
    def __init__(self):
        self.groups = {}
        self.lock = threading.Lock()
        print("Servidor de Descoberta inicializado.")

    def CreateGroup(self, request, context):
        with self.lock:
            if request.group_id in self.groups:
                return chat_pb2.CreateGroupResponse(success=False, message="Grupo já existe.")
            self.groups[request.group_id] = GroupInfo(request.group_id, request.password)
            print(f"Grupo criado: {request.group_id}")
        return chat_pb2.CreateGroupResponse(success=True, message="Grupo criado com sucesso.")

    # --- MÉTODO CORRIGIDO/ADICIONADO ---
    def ListGroups(self, request, context):
        with self.lock:
            group_ids = list(self.groups.keys())
        return chat_pb2.ListGroupsResponse(group_ids=group_ids)
    # --- FIM DA CORREÇÃO ---

    def EnterGroup(self, request, context):
        with self.lock:
            group = self.groups.get(request.group_id)
            if not group:
                return chat_pb2.EnterLeaveGroupResponse(success=False, message="Grupo não encontrado.")
            if group.password and group.password != request.password:
                return chat_pb2.EnterLeaveGroupResponse(success=False, message="Senha incorreta.")

        peer_info = chat_pb2.PeerInfo(
            user_id=request.user_id,
            address=request.peer_address,
            process_id=request.client_process_id
        )
        
        join_event = chat_pb2.GroupEvent(user_joined=peer_info)
        group.broadcast_event(join_event, exclude_user_id=request.user_id)
        
        group.add_participant(peer_info)
        
        print(f"Usuário '{request.user_id}' ({request.peer_address}) entrou no grupo '{request.group_id}'")
        return chat_pb2.EnterLeaveGroupResponse(success=True, message="Entrou no grupo com sucesso.")
    
    def GetGroupParticipants(self, request, context):
        group = self.groups.get(request.group_id)
        if not group:
            context.abort(grpc.StatusCode.NOT_FOUND, "Grupo não encontrado.")
        
        with group.lock:
            peers = list(group.participants.values())
        
        return chat_pb2.GetGroupParticipantsResponse(peers=peers)

    def LeaveGroup(self, request, context):
        group = self.groups.get(request.group_id)
        if not group:
            return chat_pb2.EnterLeaveGroupResponse(success=False, message="Grupo não encontrado.")

        leave_event = chat_pb2.GroupEvent(user_left_id=request.user_id)
        group.broadcast_event(leave_event, exclude_user_id=request.user_id)
        
        group.remove_participant(request.user_id)
        
        print(f"Usuário '{request.user_id}' saiu do grupo '{request.group_id}'")
        return chat_pb2.EnterLeaveGroupResponse(success=True, message="Saiu do grupo com sucesso.")

    def SubscribeToGroupEvents(self, request, context):
        group = self.groups.get(request.group_id)
        if not group:
            context.abort(grpc.StatusCode.NOT_FOUND, "Grupo não encontrado.")

        event_queue = queue.Queue(maxsize=100)
        group.add_subscriber(request.user_id, event_queue)
        
        print(f"Usuário '{request.user_id}' se inscreveu para eventos do grupo '{request.group_id}'.")

        try:
            while context.is_active():
                try:
                    event = event_queue.get(timeout=1)
                    if event is None:
                        break
                    yield event
                except queue.Empty:
                    if not context.is_active():
                        break
                    continue
        finally:
            print(f"Usuário '{request.user_id}' encerrou a subscrição de eventos.")
            group.remove_participant(request.user_id)

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    chat_pb2_grpc.add_DiscoveryServiceServicer_to_server(DiscoveryServiceServicer(), server)
    server.add_insecure_port('localhost:50051')
    server.start()
    print("Servidor de Descoberta rodando em localhost:50051.")
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        server.stop(0)
        print("Servidor encerrado.")

if __name__ == "__main__":
    serve()