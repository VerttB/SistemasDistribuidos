import threading
from concurrent import futures
import grpc
import chat_pb2
import chat_pb2_grpc
import queue
import time
from collections import deque

MAX_GROUP_SIZE = 20
MAX_HISTORY_SIZE = 50 # Armazena as últimas 50 mensagens

class GroupInfo:
    def __init__(self, group_id, password=None):
        self.group_id = group_id
        self.password = password
        self.participants = {}
        self.subscribers = {}
        self.lock = threading.RLock()
        self.available_slots = list(range(MAX_GROUP_SIZE))
        self.history = deque(maxlen=MAX_HISTORY_SIZE) # <-- NOVO: Armazenamento do histórico

    def add_message_to_history(self, message: chat_pb2.ChatMessage):
        with self.lock:
            self.history.append(message)

    # ... resto dos métodos da classe GroupInfo da resposta anterior ...
    def assign_slot(self):
        with self.lock:
            if not self.available_slots: return -1
            return self.available_slots.pop(0)
    def release_slot(self, slot_id):
        with self.lock: self.available_slots.append(slot_id); self.available_slots.sort()
    def add_participant(self, peer_info: chat_pb2.PeerInfo):
        with self.lock: self.participants[peer_info.user_id] = peer_info
    def remove_participant(self, user_id: str):
        with self.lock:
            if user_id in self.participants:
                slot_to_release = self.participants[user_id].process_id
                self.release_slot(slot_to_release); del self.participants[user_id]
                return slot_to_release
            return -1
    def add_subscriber(self, user_id: str, event_queue: queue.Queue):
        with self.lock: self.subscribers[user_id] = event_queue
    def remove_subscriber(self, user_id: str):
        with self.lock:
            if user_id in self.subscribers: self.subscribers[user_id].put(None); del self.subscribers[user_id]
    def broadcast_event(self, event: chat_pb2.GroupEvent, exclude_user_id: str = None):
        with self.lock:
            for uid, q in self.subscribers.items():
                if uid != exclude_user_id: q.put(event)


class DiscoveryServiceServicer(chat_pb2_grpc.DiscoveryServiceServicer):
    def __init__(self):
        self.groups = {}
        self.lock = threading.RLock()
        print("Servidor de Descoberta inicializado.")

    def CreateGroup(self, request, context):
        with self.lock:
            if request.group_id in self.groups:
                return chat_pb2.GenericResponse(success=False, message="Grupo já existe.")
            self.groups[request.group_id] = GroupInfo(request.group_id, request.password)
        return chat_pb2.GenericResponse(success=True, message="Grupo criado com sucesso.")

    def ListGroups(self, request, context):
        with self.lock: group_ids = list(self.groups.keys())
        return chat_pb2.ListGroupsResponse(group_ids=group_ids)

    def EnterGroup(self, request, context):
        with self.lock: group = self.groups.get(request.group_id)
        if not group: return chat_pb2.EnterGroupResponse(success=False, message="Grupo não encontrado.")
        if group.password and group.password != request.password: return chat_pb2.EnterGroupResponse(success=False, message="Senha incorreta.")
        
        with group.lock:
            existing_peers = list(group.participants.values())
            process_id = group.assign_slot()
            if process_id == -1: return chat_pb2.EnterGroupResponse(success=False, message="Grupo está cheio.")

            peer_info = chat_pb2.PeerInfo(user_id=request.user_id, address=request.peer_address, process_id=process_id)
            group.broadcast_event(chat_pb2.GroupEvent(user_joined=peer_info), exclude_user_id=request.user_id)
            group.add_participant(peer_info)
            
            # Recupera o histórico de mensagens para o novo usuário
            history_messages = list(group.history)

            print(f"Usuário '{request.user_id}' (slot {process_id}) entrou no grupo '{request.group_id}'")
            return chat_pb2.EnterGroupResponse(
                success=True, assigned_process_id=process_id, existing_peers=existing_peers, history=history_messages
            )

    def LogMessage(self, request: chat_pb2.ChatMessage, context):
        with self.lock: group = self.groups.get(request.group_id)
        if group: group.add_message_to_history(request)
        return chat_pb2.google_dot_protobuf_dot_empty__pb2.Empty()

    # ... resto dos métodos do servicer (LeaveGroup, SubscribeToGroupEvents) da resposta anterior ...
    def LeaveGroup(self, request, context):
        with self.lock: group = self.groups.get(request.group_id)
        if not group: return chat_pb2.GenericResponse(success=False, message="Grupo não encontrado.")
        slot_released = group.remove_participant(request.user_id)
        if slot_released != -1:
            group.broadcast_event(chat_pb2.GroupEvent(user_left_id=request.user_id), exclude_user_id=request.user_id)
            print(f"Usuário '{request.user_id}' (slot {slot_released}) saiu.")
        return chat_pb2.GenericResponse(success=True, message="Você saiu do grupo.")

    def SubscribeToGroupEvents(self, request, context):
        with self.lock: group = self.groups.get(request.group_id)
        if not group: context.abort(grpc.StatusCode.NOT_FOUND, "Grupo não encontrado.")
        event_queue = queue.Queue(maxsize=100)
        group.add_subscriber(request.user_id, event_queue)
        def on_disconnect():
            self.LeaveGroup(chat_pb2.LeaveGroupRequest(group_id=request.group_id, user_id=request.user_id), None)
            group.remove_subscriber(request.user_id)
        context.add_callback(on_disconnect)
        try:
            while True:
                event = event_queue.get();
                if event is None or not context.is_active(): break
                yield event
        except (grpc.RpcError, queue.Empty): pass


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    chat_pb2_grpc.add_DiscoveryServiceServicer_to_server(DiscoveryServiceServicer(), server)
    server.add_insecure_port('localhost:50051')
    server.start()
    print("Servidor de Descoberta rodando em localhost:50051.")
    server.wait_for_termination()

if __name__ == "__main__":
    serve()