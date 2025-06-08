import threading
import time
from concurrent import futures
import grpc
import chat_pb2
import chat_pb2_grpc
import queue

class GroupInfo:
    def __init__(self, group_id, password=None):
        self.group_id = group_id
        self.password = password
        self.participants = set()
        self.lock = threading.Lock()

        # Para o broadcast das mensagens:
        self.messages = []  # Lista de chat_pb2.ChatMessage
        self.subscribers = []  # Lista de (queue.Queue) para notificar mensagens

    def add_subscriber(self, subscriber_queue):
        with self.lock:
            self.subscribers.append(subscriber_queue)

    def remove_subscriber(self, subscriber_queue):
        with self.lock:
            if subscriber_queue in self.subscribers:
                self.subscribers.remove(subscriber_queue)

    def broadcast_message(self, message):
        with self.lock:
            self.messages.append(message)
            for queue in self.subscribers:
                queue.put(message)


class ChatServiceServicer(chat_pb2_grpc.ChatServiceServicer):
    def __init__(self):
        self.groups = {}  # map group_id -> GroupInfo
        self.lock = threading.Lock()
        print("Servidor inicializado.")

    def CreateGroup(self, request, context):
        with self.lock:
            if request.group_id in self.groups:
                return chat_pb2.CreateGroupResponse(success=False, message="Grupo já existe.")
            self.groups[request.group_id] = GroupInfo(request.group_id, request.password)
            print(f"Grupo criado: {request.group_id} (senha {'sim' if request.password else 'não'})")
        return chat_pb2.CreateGroupResponse(success=True, message="Grupo criado com sucesso.")

    def ListGroups(self, request, context):
        with self.lock:
            group_ids = list(self.groups.keys())
        return chat_pb2.ListGroupsResponse(group_ids=group_ids)

    def EnterGroup(self, request, context):
        with self.lock:
            group = self.groups.get(request.group_id)
            if not group:
                return chat_pb2.EnterLeaveGroupResponse(success=False, message="Grupo não encontrado.")
            if group.password and group.password != request.password:
                return chat_pb2.EnterLeaveGroupResponse(success=False, message="Senha incorreta.")
            with group.lock:
                group.participants.add(request.user_id)
            print(f"Usuário '{request.user_id}' entrou no grupo '{request.group_id}'")
        return chat_pb2.EnterLeaveGroupResponse(success=True, message="Entrou no grupo com sucesso.")

    def LeaveGroup(self, request, context):
        with self.lock:
            group = self.groups.get(request.group_id)
            if not group:
                return chat_pb2.EnterLeaveGroupResponse(success=False, message="Grupo não encontrado.")
            with group.lock:
                if request.user_id in group.participants:
                    group.participants.remove(request.user_id)
                    print(f"Usuário '{request.user_id}' saiu do grupo '{request.group_id}'")
                    return chat_pb2.EnterLeaveGroupResponse(success=True, message="Saiu do grupo com sucesso.")
                else:
                    return chat_pb2.EnterLeaveGroupResponse(success=False, message="Usuário não estava no grupo.")
        return chat_pb2.EnterLeaveGroupResponse(success=False, message="Erro inesperado.")

    def GetGroupParticipants(self, request, context):
        with self.lock:
            group = self.groups.get(request.group_id)
            if not group:
                context.abort(grpc.StatusCode.NOT_FOUND, "Grupo não encontrado.")
            with group.lock:
                participants = list(group.participants)
        return chat_pb2.GetGroupParticipantsResponse(user_ids=participants)

    def SendMessage(self, request, context):
        # request é do tipo ChatMessage
        with self.lock:
            group = self.groups.get(request.group_id)
            if not group:
                context.abort(grpc.StatusCode.NOT_FOUND, "Grupo não encontrado.")
            with group.lock:
                if request.user_id not in group.participants:
                    context.abort(grpc.StatusCode.PERMISSION_DENIED, "Usuário não pertence ao grupo.")

                # Armazena e notifica os inscritos
                group.broadcast_message(request)

                print(f"Mensagem recebida no grupo '{group.group_id}' de '{request.user_id}': {request.text}")

        return chat_pb2.empty_pb2.Empty()

    def SubscribeToGroup(self, request, context):
        with self.lock:
            group = self.groups.get(request.group_id)
            if not group:
                context.abort(grpc.StatusCode.NOT_FOUND, "Grupo não encontrado.")

        with group.lock:
            if request.user_id not in group.participants:
                context.abort(grpc.StatusCode.PERMISSION_DENIED, "Usuário não pertence ao grupo.")

        # Cria uma fila limitada para o subscriber, para evitar travamento se o cliente não consumir
        subscriber_queue = queue.Queue(maxsize=100)
        group.add_subscriber(subscriber_queue)

        try:
            # Enviar mensagens já armazenadas (se quiser, cuidado com mensagens antigas)
            with group.lock:
                for msg in group.messages:
                    yield msg

            while True:
                # Espera uma mensagem nova ou timeout para checar se o cliente desconectou
                try:
                    msg = subscriber_queue.get(timeout=1)
                    yield msg
                except queue.Empty:
                    # Verifica se cliente desconectou para sair do loop e limpar fila
                    if not context.is_active():
                        print(f"Cliente {request.user_id} desconectou do grupo {request.group_id}")
                        break
                    continue
        finally:
            group.remove_subscriber(subscriber_queue)


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    chat_pb2_grpc.add_ChatServiceServicer_to_server(ChatServiceServicer(), server)
    server.add_insecure_port('localhost:50051')
    server.start()
    print("Servidor iniciado em localhost:50051.")
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        print("Servidor encerrando...")
        server.stop(0)


if __name__ == "__main__":
    serve()
