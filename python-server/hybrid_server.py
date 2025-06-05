import asyncio
import json
import logging
import os
import base64
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
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "Xb7hH8MSUJpSbSDYk0k2")  # Default voice

if not ELEVENLABS_API_KEY:
    raise ValueError("ELEVENLABS_API_KEY must be set in .env file")
if not ELEVENLABS_AGENT_ID:
    raise ValueError("ELEVENLABS_AGENT_ID must be set in .env file")

async def connect_to_elevenlabs_ai():
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
        logger.error(f"Failed to connect to ElevenLabs AI: {str(e)}")
        raise

async def connect_to_elevenlabs_tts():
    """Connect to ElevenLabs TTS WebSocket."""
    uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream-input?model_id=eleven_flash_v2_5"
    
    try:
        ws = await connect(uri)
        logger.info("Successfully connected to ElevenLabs TTS")
        
        # Initialize TTS session
        init_message = {
            "text": " ",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.8,
                "use_speaker_boost": False
            },
            "generation_config": {
                "chunk_length_schedule": [120, 160, 250, 290]
            },
            "xi_api_key": ELEVENLABS_API_KEY,
        }
        await ws.send(json.dumps(init_message))
        logger.info("Initialized ElevenLabs TTS session")
        
        return ws
    except Exception as e:
        logger.error(f"Failed to connect to ElevenLabs TTS: {str(e)}")
        raise

class HybridElevenLabsRelay:
    def __init__(self):
        """Initialize the hybrid WebSocket relay server."""
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
        elevenlabs_ai_ws = None
        elevenlabs_tts_ws = None

        try:
            # Connect to both ElevenLabs AI and TTS
            elevenlabs_ai_ws = await connect_to_elevenlabs_ai()
            elevenlabs_tts_ws = await connect_to_elevenlabs_tts()
            
            self.connections[websocket] = {
                'ai': elevenlabs_ai_ws,
                'tts': elevenlabs_tts_ws
            }

            logger.info("Connected to both ElevenLabs AI and TTS successfully!")

            # Send session created event for compatibility
            session_created = {
                "type": "session.created",
                "session": {
                    "id": "elevenlabs_hybrid_session",
                    "object": "realtime.session",
                    "model": "elevenlabs-hybrid",
                    "modalities": ["text", "audio"],
                    "instructions": "ElevenLabs Hybrid Agent",
                    "voice": "elevenlabs-tts-voice",
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
                """Forward browser messages to ElevenLabs AI."""
                try:
                    while True:
                        message = await websocket.recv()
                        try:
                            event = json.loads(message)
                            event_type = event.get("type")
                            
                            if event_type == "session.update":
                                logger.info("Received session.update, acknowledging")
                                ack_event = {
                                    "type": "session.updated",
                                    "session": session_created["session"]
                                }
                                await websocket.send(json.dumps(ack_event))
                            elif event_type == "input_audio_buffer.append":
                                logger.info("Forwarding audio input to ElevenLabs AI")
                                ai_message = {
                                    "user_audio_chunk": event.get("audio", "")
                                }
                                await elevenlabs_ai_ws.send(json.dumps(ai_message))
                            elif event_type == "input_audio_buffer.commit":
                                logger.info("Committing audio buffer")
                                pass
                            elif event_type == "response.create":
                                logger.info("Creating response")
                                pass
                            else:
                                logger.info(f'Received event "{event_type}" from browser')
                                
                        except json.JSONDecodeError:
                            logger.error(f"Invalid JSON from browser: {message}")
                        except Exception as e:
                            logger.error(f"Error processing browser message: {e}")
                            
                except websockets.exceptions.ConnectionClosed as e:
                    logger.info(f"Browser connection closed: code={e.code}, reason={e.reason}")
                    raise

            async def handle_elevenlabs_ai_messages():
                """Handle messages from ElevenLabs AI and convert text to speech."""
                try:
                    while True:
                        message = await elevenlabs_ai_ws.recv()
                        try:
                            event = json.loads(message)
                            event_type = event.get("type")
                            
                            logger.info(f"Received AI event: {event_type}")
                            
                            if event_type == "agent_response":
                                # Get the text response and send to TTS
                                agent_response = event.get("agent_response_event", {})
                                text_response = agent_response.get("agent_response", "")
                                
                                if text_response:
                                    logger.info(f"Converting to speech: {text_response}")
                                    
                                    # Send text to TTS WebSocket
                                    tts_message = {
                                        "text": text_response,
                                        "flush": True
                                    }
                                    await elevenlabs_tts_ws.send(json.dumps(tts_message))
                                    
                                    # Also send text event to browser
                                    text_event = {
                                        "type": "response.text.delta",
                                        "response_id": "elevenlabs_response",
                                        "item_id": "elevenlabs_text",
                                        "output_index": 0,
                                        "content_index": 0,
                                        "delta": text_response
                                    }
                                    await websocket.send(json.dumps(text_event))
                                    logger.info("Sent text to TTS and forwarded to browser")
                                    
                            elif event_type == "user_transcript":
                                logger.info("User transcript received")
                                
                            elif event_type == "interruption" or event_type == "agent_response_correction":
                                openai_event = {
                                    "type": "conversation.interrupted"
                                }
                                await websocket.send(json.dumps(openai_event))
                                logger.info("Relayed interruption from ElevenLabs AI")
                                
                            elif event_type == "ping":
                                pass
                            else:
                                logger.info(f'Unhandled AI event "{event_type}": {message}')
                                
                        except json.JSONDecodeError:
                            logger.error(f"Invalid JSON from ElevenLabs AI: {message}")
                        except Exception as e:
                            logger.error(f"Error processing ElevenLabs AI message: {e}")
                            
                except websockets.exceptions.ConnectionClosed as e:
                    logger.info(f"ElevenLabs AI connection closed: code={e.code}, reason={e.reason}")
                    raise

            async def handle_elevenlabs_tts_messages():
                """Handle audio messages from ElevenLabs TTS."""
                try:
                    while True:
                        message = await elevenlabs_tts_ws.recv()
                        try:
                            data = json.loads(message)
                            
                            if data.get("audio"):
                                # Convert TTS audio to OpenAI format
                                audio_data = data["audio"]
                                openai_event = {
                                    "type": "response.audio.delta",
                                    "response_id": "elevenlabs_tts_response",
                                    "item_id": "elevenlabs_tts_audio",
                                    "output_index": 0,
                                    "content_index": 0,
                                    "delta": audio_data
                                }
                                await websocket.send(json.dumps(openai_event))
                                logger.info("Relayed TTS audio to browser")
                                
                            elif data.get("isFinal"):
                                # Send completion event
                                completion_event = {
                                    "type": "response.audio.done"
                                }
                                await websocket.send(json.dumps(completion_event))
                                logger.info("TTS audio generation completed")
                                
                        except json.JSONDecodeError:
                            logger.error(f"Invalid JSON from ElevenLabs TTS: {message}")
                        except Exception as e:
                            logger.error(f"Error processing ElevenLabs TTS message: {e}")
                            
                except websockets.exceptions.ConnectionClosed as e:
                    logger.info(f"ElevenLabs TTS connection closed: code={e.code}, reason={e.reason}")
                    raise

            try:
                await asyncio.gather(
                    handle_browser_messages(),
                    handle_elevenlabs_ai_messages(),
                    handle_elevenlabs_tts_messages()
                )
            except websockets.exceptions.ConnectionClosed:
                logger.info("Connection closed, cleaning up")

        except Exception as e:
            logger.error(f"Error handling connection: {str(e)}")
            if not websocket.closed:
                await websocket.close(1011, str(e))
        finally:
            # Cleanup connections
            if websocket in self.connections:
                connections = self.connections[websocket]
                if connections.get('ai') and not connections['ai'].closed:
                    await connections['ai'].close(1000, "Normal closure")
                if connections.get('tts') and not connections['tts'].closed:
                    await connections['tts'].close(1000, "Normal closure")
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
            logger.info(f"Hybrid ElevenLabs server started on ws://0.0.0.0:{PORT}")
            await asyncio.Future()

def main():
    """Main entry point for the hybrid server."""
    relay = HybridElevenLabsRelay()
    try:
        asyncio.run(relay.serve())
    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    finally:
        logger.info("Server shutdown complete")

if __name__ == "__main__":
    main() 