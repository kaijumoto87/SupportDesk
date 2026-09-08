from django.urls import path
from .consumers import SupportDeskConsumer

websocket_urlpatterns = [path("ws/supportdesk/", SupportDeskConsumer.as_asgi())]
