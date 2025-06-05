import asyncio
import json
import logging
import os
from dotenv import load_dotenv
import websockets
from websockets.legacy.server import WebSocketServerProtocol, serve
from websockets.legacy.client import connect

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

load_dotenv()
PORT = int(os.getenv("PORT", 3001))
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID")

if not ELEVENLABS_API_KEY:
    raise ValueError("ELEVENLABS_API_KEY must be set in .env file")
if not ELEVENLABS_AGENT_ID:
    raise ValueError("ELEVENLABS_AGENT_ID must be set in .env file")


async def connect_to_elevenlabs():
    """Connect to ElevenLabs Conversational AI WebSocket."""
    uri = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={ELEVENLABS_AGENT_ID}"

    try:
        ws = await connect(
            uri,
            extra_headers={
                "xi-api-key": ELEVENLABS_API_KEY,
            },
        )
        logger.info("Successfully connected to ElevenLabs Conversational AI")
        return ws
    except Exception as e:
        logger.error(f"Failed to connect to ElevenLabs: {str(e)}")
        raise


class ElevenLabsRelay:
    def __init__(self):
        """Initialize the WebSocket relay server."""
        self.connections = {}

    async def handle_browser_connection(
        self, websocket: WebSocketServerProtocol, path: str
    ):
        """Handle a connection from the browser."""
        base_path = path.split("?")[0]
        if base_path != "/":
            logger.error(f"Invalid path: {path}")
            await websocket.close(1008, "Invalid path")
            return

        logger.info(f"Browser connected from {websocket.remote_address}")
        elevenlabs_ws = None

        try:
            # Connect to ElevenLabs Conversational AI
            elevenlabs_ws = await connect_to_elevenlabs()
            self.connections[websocket] = elevenlabs_ws

            logger.info("Connected to ElevenLabs successfully!")

            # Send a session created event to maintain compatibility with frontend
            session_created = {
                "type": "session.created",
                "session": {
                    "id": "elevenlabs_session",
                    "object": "realtime.session",
                    "model": "elevenlabs-conversational-ai",
                    "modalities": ["text", "audio"],
                    "instructions": "ElevenLabs Conversational AI Agent",
                    "voice": "elevenlabs-agent-voice",
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "input_audio_transcription": None,
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 200
                    },
                    "tools": [],
                    "tool_choice": "auto",
                    "temperature": 0.8,
                    "max_response_output_tokens": "inf"
                }
            }
            await websocket.send(json.dumps(session_created))
            logger.info("Sent session.created to browser")

            async def handle_browser_messages():
                """Forward browser messages to ElevenLabs."""
                try:
                    while True:
                        message = await websocket.recv()
                        try:
                            event = json.loads(message)
                            event_type = event.get("type")
                            
                            # Handle different message types
                            if event_type == "session.update":
                                # ElevenLabs doesn't need session updates, send acknowledgment
                                logger.info("Received session.update, acknowledging")
                                ack_event = {
                                    "type": "session.updated",
                                    "session": session_created["session"]
                                }
                                await websocket.send(json.dumps(ack_event))
                            elif event_type == "input_audio_buffer.append":
                                # Forward audio data to ElevenLabs
                                logger.info("Forwarding audio input to ElevenLabs")
                                # Convert OpenAI format to ElevenLabs format if needed
                                elevenlabs_message = {
                                    "user_audio_chunk": event.get("audio", "")
                                }
                                await elevenlabs_ws.send(json.dumps(elevenlabs_message))
                            elif event_type == "input_audio_buffer.commit":
                                # Signal end of audio input
                                logger.info("Committing audio buffer")
                                # ElevenLabs handles this automatically
                                pass
                            elif event_type == "response.create":
                                # Trigger response generation
                                logger.info("Creating response")
                                # ElevenLabs handles this automatically with VAD
                                pass
                            else:
                                # Forward other events directly or log them
                                logger.info(f'Received event "{event_type}" from browser')
                                
                        except json.JSONDecodeError:
                            logger.error(f"Invalid JSON from browser: {message}")
                        except Exception as e:
                            logger.error(f"Error processing browser message: {e}")
                            
                except websockets.exceptions.ConnectionClosed as e:
                    logger.info(f"Browser connection closed: code={e.code}, reason={e.reason}")
                    raise

            async def handle_elevenlabs_messages():
                """Forward ElevenLabs messages to browser."""
                try:
                    while True:
                        message = await elevenlabs_ws.recv()
                        try:
                            event = json.loads(message)
                            event_type = event.get("type")
                            
                            # Convert ElevenLabs events to OpenAI-compatible format
                            if event_type == "audio":
                                # Convert ElevenLabs audio to OpenAI format
                                openai_event = {
                                    "type": "response.audio.delta",
                                    "response_id": "elevenlabs_response",
                                    "item_id": "elevenlabs_audio",
                                    "output_index": 0,
                                    "content_index": 0,
                                    "delta": event.get("audio_event", {}).get("audio_base_64", "")
                                }
                                await websocket.send(json.dumps(openai_event))
                                logger.info("Relayed audio from ElevenLabs")
                            elif event_type == "message":
                                # Handle text messages
                                message_content = event.get("message", {})
                                if message_content.get("role") == "assistant":
                                    openai_event = {
                                        "type": "response.text.delta",
                                        "response_id": "elevenlabs_response", 
                                        "item_id": "elevenlabs_text",
                                        "output_index": 0,
                                        "content_index": 0,
                                        "delta": message_content.get("content", "")
                                    }
                                    await websocket.send(json.dumps(openai_event))
                                    logger.info("Relayed text from ElevenLabs")
                            elif event_type == "interruption":
                                # Handle interruptions
                                openai_event = {
                                    "type": "conversation.interrupted"
                                }
                                await websocket.send(json.dumps(openai_event))
                                logger.info("Relayed interruption from ElevenLabs")
                            else:
                                # Forward other events or log them
                                logger.info(f'Received event "{event_type}" from ElevenLabs: {message}')
                                # For compatibility, forward as-is
                            await websocket.send(message)
                                
                        except json.JSONDecodeError:
                            logger.error(f"Invalid JSON from ElevenLabs: {message}")
                        except Exception as e:
                            logger.error(f"Error processing ElevenLabs message: {e}")
                            
                except websockets.exceptions.ConnectionClosed as e:
                    logger.info(f"ElevenLabs connection closed: code={e.code}, reason={e.reason}")
                    raise

            try:
                await asyncio.gather(
                    handle_browser_messages(),
                    handle_elevenlabs_messages()
                )
            except websockets.exceptions.ConnectionClosed:
                logger.info("Connection closed, cleaning up")

        except Exception as e:
            logger.error(f"Error handling connection: {str(e)}")
            if not websocket.closed:
                await websocket.close(1011, str(e))
        finally:
            # Cleanup
            if websocket in self.connections:
                if elevenlabs_ws and not elevenlabs_ws.closed:
                    await elevenlabs_ws.close(1000, "Normal closure")
                del self.connections[websocket]
            if not websocket.closed:
                await websocket.close(1000, "Normal closure")

    async def serve(self):
        """Start the WebSocket relay server."""
        async with serve(
            self.handle_browser_connection,
            "0.0.0.0",
            PORT,
            ping_interval=20,
            ping_timeout=20,
            subprotocols=["realtime"],
        ):
            logger.info(f"ElevenLabs relay server started on ws://0.0.0.0:{PORT}")
            await asyncio.Future()


def main():
    """Main entry point for the WebSocket relay server."""
    relay = ElevenLabsRelay()
    try:
        asyncio.run(relay.serve())
    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    finally:
        logger.info("Server shutdown complete")


if __name__ == "__main__":
    main() 