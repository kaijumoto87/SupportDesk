from channels.generic.websocket import AsyncJsonWebsocketConsumer


class SupportDeskConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close()
            return
        self.group = f"user_{user.pk}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def support_event(self, event):
        await self.send_json(event["payload"])

    async def receive_json(self, content, **kwargs):
        if content.get("type") == "typing" and str(content.get("target", "")).isdigit():
            await self.channel_layer.group_send(f"user_{content['target']}", {"type": "support.event", "payload": {"type": "typing", "name": self.scope["user"].get_full_name() or self.scope["user"].username}})
