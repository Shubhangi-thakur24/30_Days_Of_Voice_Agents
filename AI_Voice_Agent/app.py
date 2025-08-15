from flask import Flask, request, jsonify, render_template, send_from_directory
import requests
import os
from dotenv import load_dotenv
import flet as ft
from werkzeug.utils import secure_filename
from datetime import datetime
import assemblyai as aai
import google.generativeai as genai
import logging  # Add this import
import io
# Initialize logger
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Load environment variables
load_dotenv()

app = Flask(__name__)

from flask_cors import CORS
CORS(app)
# Update this in your configuration
MURF_BASE_URL = "https://api.murf.ai/v1/speech"
GENERATE_ENDPOINT = f"{MURF_BASE_URL}/generate-with-key"  
MURF_API_KEY = os.getenv("MURF_API_KEY")
AAI_API_KEY = os.getenv("AAI_API_KEY")   # Make sure to set this in your .env file
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if AAI_API_KEY:
    aai.settings.api_key = AAI_API_KEY
else:
    logger.error("AssemblyAI API key not found in environment variables")

if not all([AAI_API_KEY, MURF_API_KEY, GEMINI_API_KEY]):
    raise ValueError("API keys must be set in the .env file")

# Right after loading .env
if not all([AAI_API_KEY, MURF_API_KEY, GEMINI_API_KEY]):
    missing = []
    if not AAI_API_KEY: missing.append("AssemblyAI")
    if not MURF_API_KEY: missing.append("Murf.ai")
    if not GEMINI_API_KEY: missing.append("Gemini")
    logger.critical(f"Missing API keys for: {', '.join(missing)}")

genai.configure(api_key=GEMINI_API_KEY)

# Replace your current genai configuration with this:
genai.configure(
    api_key=os.getenv("GEMINI_API_KEY"),
    transport='rest',
    client_options={
        "api_endpoint": "generativelanguage.googleapis.com/v1beta"
    }
)

aai.settings.api_key = AAI_API_KEY
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-pro')

try:
    model = genai.GenerativeModel('gemini-pro')
    print("Gemini model initialized successfully")
except Exception as e:
    print(f"Failed to initialize Gemini model: {str(e)}")
# Configuration for file uploads
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'wav', 'mp3', 'ogg', 'webm'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB limit

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
chat_history_store = {}
# Murf API Configuration
MURF_BASE_URL = "https://api.murf.ai/v1"
GENERATE_ENDPOINT = f"{MURF_BASE_URL}/speech/generate"
VOICES_ENDPOINT = f"{MURF_BASE_URL}/speech/voices"

# Common valid voice IDs
DEFAULT_VOICES = [
    "en-US-Natalie",  # American English - Female
    "en-US-Mike",     # American English - Male
    "en-GB-Lucy",     # British English - Female
    "hi-IN-Priya",    # Hindi - Female
    "es-ES-Enrique"   # Spanish - Male
]

def get_auth_headers():
    """Generate proper authentication headers"""
    return {
        "api-key": MURF_API_KEY,
        "Content-Type": "application/json"
    }

def get_valid_voices(force_refresh=False):
    """Fetch and cache available voice IDs from Murf API"""
    global DEFAULT_VOICES
    
    try:
        response = requests.get(
            VOICES_ENDPOINT,
            headers=get_auth_headers(),
            timeout=5
        )
        
        if response.status_code == 200:
            voices_data = response.json()
            
            # Handle different API response formats
            if isinstance(voices_data, list):
                voice_ids = [v.get("voiceId") for v in voices_data if v.get("voiceId")]
            elif isinstance(voices_data, dict):
                voice_ids = [v.get("voiceId") for v in voices_data.get("voices", []) if v.get("voiceId")]
            else:
                voice_ids = DEFAULT_VOICES
            
            if voice_ids:  # Update defaults if we got valid voices
                DEFAULT_VOICES = voice_ids
            return voice_ids
            
    except Exception as e:
        print(f"Voice fetch error, using defaults: {str(e)}")
    
    return DEFAULT_VOICES



@app.route('/llm/query', methods=['POST'])
def query_llm():
    
    try:
        # Check if audio file was uploaded
        if 'audio' not in request.files:
            return jsonify({"error": "No audio file provided"}), 400
        
        audio_file = request.files['audio']
        
        # Validate file
        if audio_file.filename == '':
            return jsonify({"error": "No selected file"}), 400
        
        if not allowed_file(audio_file.filename):
            return jsonify({"error": "Invalid file type"}), 400

        # Step 1: Transcribe the audio
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(audio_file.read())
        
        if transcript.error:
            return jsonify({"error": "Transcription failed", "message": transcript.error}), 500
        
        transcription_text = transcript.text
        if not transcription_text.strip():
            return jsonify({"error": "Empty transcription", "message": "No speech detected"}), 400

        # Step 2: Generate LLM response (single attempt with proper error handling)
        try:
            llm_response = model.generate_content(transcription_text)
            response_text = llm_response.text
        except Exception as e:
            return jsonify({
                "error": "LLM API Error",
                "message": str(e),
                "type": type(e).__name__,
                "details": "Failed to generate response from Gemini"
            }), 500

        # Step 3: Generate speech from response (handle 3000 char limit)
        try:
            if len(response_text) > 3000:
                # Split into chunks of 3000 characters
                chunks = [response_text[i:i+3000] for i in range(0, len(response_text), 3000)]
                audio_urls = []
                
                for chunk in chunks:
                    murf_response = requests.post(
                        GENERATE_ENDPOINT,
                        json={
                            "text": chunk,
                            "voiceId": "en-US-Natalie",  # Default voice
                            "format": "mp3",
                            "sampleRate": 24000
                        },
                        headers=get_auth_headers(),
                        timeout=10  # Added timeout
                    )
                    
                    if murf_response.status_code != 200:
                        return jsonify({
                            "error": "Murf API error",
                            "message": murf_response.text,
                            "status": murf_response.status_code,
                            "chunk": f"{len(chunk)} chars"
                        }), 500
                    
                    audio_url = murf_response.json().get("audioFile")
                    if not audio_url:
                        return jsonify({
                            "error": "Invalid Murf response",
                            "message": "No audio URL returned",
                            "response": murf_response.json()
                        }), 500
                    
                    audio_urls.append(audio_url)
                
                # Return all audio URLs if multiple chunks
                if len(audio_urls) > 1:
                    return jsonify({
                        "success": True,
                        "audio_urls": audio_urls,  # Client should handle multiple files
                        "transcription": transcription_text,
                        "llm_response": response_text,
                        "warning": "Response exceeded 3000 characters - multiple audio files returned"
                    })
                audio_url = audio_urls[0]
            else:
                # Single request for shorter responses
                murf_response = requests.post(
                    GENERATE_ENDPOINT,
                    json={
                        "text": response_text,
                        "voiceId": "en-US-Natalie",
                        "format": "mp3",
                        "sampleRate": 24000
                    },
                    headers=get_auth_headers(),
                    timeout=10  # Added timeout
                )
                
                if murf_response.status_code != 200:
                    return jsonify({
                        "error": "Murf API error",
                        "message": murf_response.text,
                        "status": murf_response.status_code
                    }), 500
                
                audio_url = murf_response.json().get("audioFile")
                if not audio_url:
                    return jsonify({
                        "error": "Invalid Murf response",
                        "message": "No audio URL returned",
                        "response": murf_response.json()
                    }), 500
                if request.is_json:
                    data = request.get_json()
                    input_text = data.get('text', '')
                else:
                    pass
                if 'audio' in request.files:
                    audio_file = request.files['audio']
        # Process audio
                else:
        # Handle JSON case
                    pass
            return jsonify({
                "success": True,
                "audio_url": audio_url,
                "transcription": transcription_text,
                "llm_response": response_text
            })

        except requests.exceptions.RequestException as e:
            return jsonify({
                "error": "Murf API Connection Error",
                "message": str(e),
                "type": type(e).__name__
            }), 503  # Service Unavailable
        except Exception as e:
            return jsonify({
                "error": "Audio Generation Error",
                "message": str(e),
                "type": type(e).__name__
            }), 500

    except Exception as e:
        return jsonify({
            "error": "Internal Server Error",
            "message": str(e),
            "type": type(e).__name__,
            "details": "Unexpected error in processing pipeline"
        }), 500
@app.route('/test_pipeline', methods=['POST'])
def test_pipeline():
    """Test endpoint for the full pipeline"""
    try:
        # Simulate the pipeline steps
        test_text = "Hello, how are you today?"
        
        # Step 1: LLM response
        llm_response = model.generate_content(test_text)
        response_text = llm_response.text
        
        # Step 2: Generate speech
        murf_response = requests.post(
            GENERATE_ENDPOINT,
            json={
                "text": response_text[:3000],  # Ensure we don't exceed limit
                "voiceId": "en-US-Natalie",
                "format": "mp3",
                "sampleRate": 24000
            },
            headers=get_auth_headers()
        )
        
        if murf_response.status_code != 200:
            return jsonify({
                "error": "Murf API error",
                "message": murf_response.text
            }), 500
        
        return jsonify({
            "success": True,
            "input_text": test_text,
            "llm_response": response_text,
            "audio_url": murf_response.json().get("audioFile")
        })
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500
# Text-to-Speech Endpoint (Day 2 Task)
@app.route('/generate_audio', methods=['POST'])
def generate_audio():
    try:
        data = request.get_json()
        text = data.get('text')
        requested_voice = data.get('voice', 'en-US-Natalie')

        if not text:
            return jsonify({"error": "Text is required"}), 400

        print(f"Generating audio for: {text[:50]}...")
        
        payload = {
            "text": text,
            "voiceId": requested_voice,
            "format": "mp3",
            "sampleRate": 24000
        }

        response = requests.post(
            "https://api.murf.ai/v1/speech/generate-with-key",
            json=payload,
            headers={
                "api-key": MURF_API_KEY,
                "Content-Type": "application/json"
            },
            timeout=10
        )

        print(f"Murf API response: {response.status_code}, {response.text[:200]}...")

        if response.status_code == 200:
            response_data = response.json()
            
            # Updated URL extraction for current Murf API response
            audio_url = response_data.get("audioFile")  # Changed from audioStreamUrl
            
            if not audio_url:
                print("No audio URL found. Full response:", response_data)
                return jsonify({
                    "error": "No audio URL in response",
                    "debug": response_data
                }), 500
                
            return jsonify({
                "success": True,
                "audio_url": audio_url,  # Keep this field name consistent
                "voice_used": requested_voice
            })
        else:
            return jsonify({
                "error": "Murf API Error",
                "status": response.status_code,
                "response": response.text
            }), response.status_code

    except Exception as e:
        return jsonify({
            "error": "Internal server error",
            "message": str(e)
        }), 500
# Day 5: Audio Upload Endpoint
@app.route('/upload_audio', methods=['POST'])
def upload_audio():
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400
    
    audio_file = request.files['audio']
    
    if audio_file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if audio_file and allowed_file(audio_file.filename):
        # Generate unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"recording_{timestamp}_{secure_filename(audio_file.filename)}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Save the file
        audio_file.save(filepath)
        
        # Get file info
        file_size = os.path.getsize(filepath)
        
        return jsonify({
            'status': 'success',
            'filename': filename,
            'content_type': audio_file.content_type,
            'size': file_size,
            'message': 'Audio uploaded successfully'
        })
    
    return jsonify({'error': 'Invalid file type'}), 400

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Day 6: Transcription Endpoint
@app.route('/transcribe/file', methods=['POST'])
def transcribe_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    audio_file = request.files['file']
    
    # Create a transcriber object
    transcriber = aai.Transcriber()
    
    try:
        # Transcribe the audio file directly from binary data
        transcript = transcriber.transcribe(audio_file.read())
        
        if transcript.error:
            return jsonify({"error": transcript.error}), 500
            
        return jsonify({
            "transcription": transcript.text,
            "status": "success",
            "message": "Audio transcribed successfully"
        })
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Utility function for fallback audio (returns None or a placeholder URL)
def generate_fallback_audio(message: str, voice_id="en-US-Natalie"):
    """More robust fallback audio generation"""
    try:
        if not MURF_API_KEY:
            return None
            
        response = requests.post(
            GENERATE_ENDPOINT,
            json={
                "text": message[:1000],  # Safe truncation
                "voiceId": voice_id,
                "format": "mp3",
                "sampleRate": 24000
            },
            headers=get_auth_headers(),
            timeout=10
        )
        
        if response.status_code == 200:
            return response.json().get("audioFile")
        
        logger.warning(f"Fallback audio failed: {response.status_code} {response.text}")
        return None
        
    except Exception as e:
        logger.error(f"Critical fallback failure: {str(e)}")
        return None


@app.route('/agent/chat/<session_id>', methods=['POST'])
def chat_with_history(session_id):
    # Service availability check
    if not all([AAI_API_KEY, MURF_API_KEY, GEMINI_API_KEY]):
        return jsonify({
            "error": "service_unavailable",
            "message": "Required APIs not configured",
            "audio_url": generate_fallback_audio("System maintenance in progress")
        }), 503

    try:
        # Validate audio file
        if 'audio' not in request.files:
            return jsonify({
                "error": "invalid_input",
                "message": "No audio file provided",
                "audio_url": generate_fallback_audio("Please send an audio message")
            }), 400
            
        audio_file = request.files['audio']
        if not allowed_file(audio_file.filename):
            return jsonify({
                "error": "invalid_file_type",
                "message": f"Allowed formats: {ALLOWED_EXTENSIONS}",
                "audio_url": generate_fallback_audio("Unsupported file format")
            }), 400

        # Initialize chat history
        if session_id not in chat_history_store:
            chat_history_store[session_id] = []
        chat_history = chat_history_store[session_id]

        # Transcribe audio
        audio_data = audio_file.read()
        if not audio_data:
            return jsonify({
                "error": "empty_audio",
                "message": "Empty audio file",
                "audio_url": generate_fallback_audio("The audio contains no data")
            }), 400

        transcript = aai.Transcriber().transcribe(io.BytesIO(audio_data))
        if transcript.error:
            logger.error(f"Transcription failed: {transcript.error}")
            return jsonify({
                "error": "transcription_failed",
                "message": str(transcript.error),
                "audio_url": generate_fallback_audio("I couldn't understand that audio")
            }), 500

        # Generate LLM response
        try:
            chat = model.start_chat(history=[
                {"role": msg["role"], "parts": [msg["content"]]} 
                for msg in chat_history
            ])
            response = chat.send_message(transcript.text)
            response_text = response.text
        except Exception as e:
            logger.error(f"LLM error: {str(e)}")
            return jsonify({
                "error": "llm_error",
                "message": str(e),
                "audio_url": generate_fallback_audio("I'm having trouble thinking right now")
            }), 500

        # Generate TTS audio
        try:
            tts_response = requests.post(
                GENERATE_ENDPOINT,
                json={
                    "text": response_text[:3000],  # Safe truncation
                    "voiceId": "en-US-Natalie",
                    "format": "mp3",
                    "sampleRate": 24000
                },
                headers=get_auth_headers(),
                timeout=15
            )
            
            if tts_response.status_code != 200:
                raise Exception(tts_response.text)
                
            audio_url = tts_response.json().get("audioFile")
            if not audio_url:
                raise Exception("No audio URL in response")
                
        except Exception as e:
            logger.error(f"TTS generation failed: {str(e)}")
            return jsonify({
                "error": "tts_failed",
                "message": str(e),
                "audio_url": generate_fallback_audio("I can't speak right now")
            }), 500

        # Update conversation history
        chat_history.extend([
            {"role": "user", "content": transcript.text},
            {"role": "model", "content": response_text}
        ])

        return jsonify({
            "success": True,
            "audio_url": audio_url,
            "transcription": transcript.text,
            "llm_response": response_text,
            "session_id": session_id
        })

    except Exception as e:
        logger.critical(f"Unexpected error in chat: {str(e)}")
        return jsonify({
            "error": "server_error",
            "message": "Internal server error",
            "audio_url": generate_fallback_audio("Something went wrong")
        }), 500
def generate_session_id():
    """Generate a unique session ID based on the current timestamp"""
    return datetime.now().strftime("%Y%m%d%H%M%S%f")
@app.route('/api/start-recording', methods=['POST'])
def handle_recording_start():
    """Endpoint called when recording starts"""
    return jsonify({
        "status": "recording_started",
        "message": "Recording session initialized"
    })

@app.route('/api/stop-recording', methods=['POST'])
def handle_recording_stop():
    """Endpoint called when recording stops"""
    if 'audio' not in request.files:
        return jsonify({
            "error": "no_audio",
            "message": "No audio file received"
        }), 400
        
    audio_file = request.files['audio']
    
    # Process the recording through our existing pipeline
    session_id = generate_session_id()
    response = chat_with_history(session_id)
    
    # Return the response from chat_with_history
    return response
# Day 7: Echo Bot v2 Endpoint
@app.route('/tts/echo', methods=['POST'])
def echo_tts():
    # Validate audio file presence
    if 'audio' not in request.files:
        fallback_url = generate_fallback_audio("No audio file was provided.")
        return jsonify({
            'error': 'No audio file provided',
            'message': 'Please record or upload an audio file',
            'audio_url': fallback_url or ""
        }), 400
    
    audio_file = request.files['audio']
    
    # Validate file name
    if audio_file.filename == '':
        fallback_url = generate_fallback_audio("The selected file has no name.")
        return jsonify({
            'error': 'No selected file',
            'message': 'Please select a valid audio file',
            'audio_url': fallback_url or ""
        }), 400
    
    # Validate file extension
    if not allowed_file(audio_file.filename):
        fallback_url = generate_fallback_audio("Invalid file type was uploaded.")
        return jsonify({
            'error': 'Invalid file type',
            'message': f'Allowed formats: {", ".join(ALLOWED_EXTENSIONS)}',
            'audio_url': fallback_url or ""
        }), 400
    
    try:
        # Read audio file content
        audio_data = audio_file.read()
        if not audio_data:
            fallback_url = generate_fallback_audio("The audio file was empty.")
            return jsonify({
                'error': 'Empty audio file',
                'message': 'The uploaded file contains no data',
                'audio_url': fallback_url or ""
            }), 400

        # Reset file pointer after reading
        audio_file.seek(0)

        # Step 1: Transcribe the audio
        try:
            transcriber = aai.Transcriber()
            transcript = transcriber.transcribe(audio_data)
            
            if transcript.error:
                fallback_url = generate_fallback_audio("I couldn't understand the audio.")
                return jsonify({
                    "error": "Transcription failed",
                    "message": transcript.error,
                    "audio_url": fallback_url or ""
                }), 500
            
            transcription_text = transcript.text
            if not transcription_text.strip():
                fallback_url = generate_fallback_audio("No speech was detected in the audio.")
                return jsonify({
                    "error": "Empty transcription result",
                    "message": "The audio file didn't contain any recognizable speech",
                    "audio_url": fallback_url or ""
                }), 400

        except Exception as e:
            logger.error(f"Transcription error: {str(e)}")
            fallback_url = generate_fallback_audio("I'm having trouble understanding the audio.")
            return jsonify({
                "error": "Transcription service error",
                "message": str(e),
                "audio_url": fallback_url or ""
            }), 500

        # Step 2: Generate TTS
        try:
            valid_voices = get_valid_voices()
            if not valid_voices:
                fallback_url = generate_fallback_audio("Voice options are currently unavailable.")
                return jsonify({
                    "error": "No available voices",
                    "message": "Could not retrieve valid voices from Murf API",
                    "audio_url": fallback_url or ""
                }), 500
                
            default_voice = "en-US-Natalie" if "en-US-Natalie" in valid_voices else valid_voices[0]
            
            payload = {
                "text": transcription_text[:3000],  # Ensure we don't exceed API limits
                "voiceId": default_voice,
                "format": "mp3",
                "sampleRate": 24000
            }

            response = requests.post(
                GENERATE_ENDPOINT,
                json=payload,
                headers=get_auth_headers(),
                timeout=15
            )
            
            if response.status_code != 200:
                fallback_url = generate_fallback_audio("I'm having trouble generating a response.")
                return jsonify({
                    "error": "Murf API error",
                    "message": response.text,
                    "status_code": response.status_code,
                    "audio_url": fallback_url or ""
                }), 502
            
            response_data = response.json()
            audio_url = (response_data.get("audioFile") or 
                       response_data.get("audioStreamUrl") or 
                       response_data.get("url") or 
                       response_data.get("audio_url"))
            
            if not audio_url:
                fallback_url = generate_fallback_audio("Response generation failed.")
                return jsonify({
                    "error": "No audio URL in response",
                    "message": "TTS service returned no audio URL",
                    "audio_url": fallback_url or "",
                    "debug": {
                        "response_keys": list(response_data.keys()),
                        "suggested_fields": ["audioFile", "audioStreamUrl", "url", "audio_url"]
                    }
                }), 500
                
            return jsonify({
                "success": True,
                "audio_url": audio_url,
                "transcription": transcription_text,
                "voice_used": default_voice,
                "text_length": len(transcription_text)
            })

        except requests.exceptions.Timeout:
            fallback_url = generate_fallback_audio("The voice service is taking too long to respond.")
            return jsonify({
                "error": "Murf API timeout",
                "message": "The TTS service didn't respond in time",
                "audio_url": fallback_url or ""
            }), 504
            
        except requests.exceptions.RequestException as e:
            fallback_url = generate_fallback_audio("Voice service is currently unavailable.")
            return jsonify({
                "error": "Murf API request failed",
                "message": str(e),
                "audio_url": fallback_url or "",
                "details": {
                    "endpoint": GENERATE_ENDPOINT,
                    "timeout": 15,
                    "voice_used": default_voice
                }
            }), 502

    except Exception as e:
        logger.error(f"Unexpected error in echo_tts: {str(e)}")
        fallback_url = generate_fallback_audio("An unexpected error occurred.")
        return jsonify({
            "error": "Internal server error",
            "message": str(e),
            "type": type(e).__name__,
            "audio_url": fallback_url or ""
        }), 500

def transcribe_audio(audio_file):
    """Transcribe audio using AssemblyAI"""
    try:
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(audio_file.read())
        
        if transcript.error:
            raise Exception(f"Transcription failed: {transcript.error}")
            
        return transcript.text
    except Exception as e:
        logger.error(f"Transcription error: {str(e)}")
        raise Exception("Could not transcribe audio")

def get_ai_response(text):
    """Get response from Gemini AI"""
    try:
        response = model.generate_content(text)
        return response.text
    except Exception as e:
        logger.error(f"AI response error: {str(e)}")
        raise Exception("Could not generate AI response")

def text_to_speech(text):
    """Convert text to speech using Murf.ai"""
    try:
        response = requests.post(
            GENERATE_ENDPOINT,
            json={
                "text": text[:3000],  # Limit to 3000 chars
                "voiceId": "en-US-Natalie",
                "format": "mp3",
                "sampleRate": 24000
            },
            headers=get_auth_headers(),
            timeout=15
        )
        
        if response.status_code != 200:
            raise Exception(f"TTS API error: {response.text}")
            
        return response.json().get("audioFile")
    except Exception as e:
        logger.error(f"TTS error: {str(e)}")
        raise Exception("Could not generate speech")
@app.route('/api/process-audio', methods=['POST'])
def process_audio():
    if 'audio' not in request.files:
        return jsonify({"error": "No audio file provided"}), 400
        
    audio_file = request.files['audio']
    
    try:
        # 1. Transcribe audio
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(audio_file.read())
        
        if transcript.error:
            return jsonify({
                "error": "transcription_failed",
                "message": transcript.error
            }), 500
            
        # 2. Get AI response
        response = model.generate_content(transcript.text)
        
        # 3. Generate speech
        tts_response = requests.post(
            GENERATE_ENDPOINT,
            json={
                "text": response.text[:3000],
                "voiceId": "en-US-Natalie",
                "format": "mp3",
                "sampleRate": 24000
            },
            headers=get_auth_headers(),
            timeout=15
        )
        
        if tts_response.status_code != 200:
            return jsonify({
                "error": "tts_failed",
                "message": tts_response.text
            }), 500
            
        return jsonify({
            "success": True,
            "transcription": transcript.text,
            "response": response.text,
            "audio_url": tts_response.json().get("audioFile")
        })
        
    except Exception as e:
        return jsonify({
            "error": "processing_error",
            "message": str(e)
        }), 500
 
@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico')

# Voice List Endpoint
@app.route('/get_voices', methods=['GET'])
def list_voices():
    """Endpoint to get available voices"""
    voices = get_valid_voices()
    return jsonify({"voices": voices})

# Flet Application
def flet_app(page: ft.Page):
    """Updated Flet UI with full pipeline integration"""
    page.title = "AI Voice Agent (Day 9)"
    page.vertical_alignment = ft.MainAxisAlignment.CENTER
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 40
    
    # UI Elements
    title = ft.Text("AI Voice Agent - Full Pipeline", size=30, weight=ft.FontWeight.BOLD, color="#FFD700")
    
    # Recording elements
    start_recording_btn = ft.ElevatedButton("Start Recording")
    stop_recording_btn = ft.ElevatedButton("Stop Recording", disabled=True)
    recording_status = ft.Text()
    response_player = ft.Audio(autoplay=True)
    transcription_display = ft.Text("Transcription will appear here", width=400)
    llm_response_display = ft.Text("LLM response will appear here", width=400)
    
    # Voice dropdown
    voice_dropdown = ft.Dropdown(
        label="Select Voice",
        width=400,
        options=[ft.dropdown.Option(v) for v in DEFAULT_VOICES]
    )
    
    # Get voices and populate dropdown
    def get_voices():
        try:
            response = requests.get(f"http://{request.host}/get_voices")
            if response.status_code == 200:
                voices = response.json().get('voices', DEFAULT_VOICES)
                voice_dropdown.options = [ft.dropdown.Option(voice) for voice in voices]
                page.update()
        except Exception as e:
            print(f"Error getting voices: {e}")
    
    # Initialize voices
    get_voices()
    
    # Recording functionality
    def start_recording(e):
        recording_status.value = "Recording... (Note: Actual recording requires browser implementation)"
        start_recording_btn.disabled = True
        stop_recording_btn.disabled = False
        page.update()
    
    def stop_recording(e):
        recording_status.value = "Processing recording..."
        start_recording_btn.disabled = False
        stop_recording_btn.disabled = True
        page.update()
        
        # Simulate file upload (in a real app, you'd use actual recording data)
        fake_audio = open("sample.wav", "rb") if os.path.exists("sample.wav") else None
        
        if fake_audio:
            try:
                # Send to LLM pipeline endpoint
                files = {'audio': fake_audio}
                response = requests.post(
                    f"http://{request.host}/llm/query",
                    files=files
                )
                
                if response.status_code == 200:
                    data = response.json()
                    transcription_display.value = f"Transcription: {data.get('transcription', '')}"
                    llm_response_display.value = f"LLM Response: {data.get('llm_response', '')}"
                    response_player.src = data.get('audio_url')
                    recording_status.value = "Processing complete!"
                else:
                    recording_status.value = f"Error: {response.text}"
                
            except Exception as e:
                recording_status.value = f"Error: {str(e)}"
        else:
            recording_status.value = "No sample file found (would use actual recording in real app)"
        
        page.update()
    
    # Set up button handlers
    start_recording_btn.on_click = start_recording
    stop_recording_btn.on_click = stop_recording
    
    # Add all controls to the page
    page.add(
        title,
        ft.Divider(),
        ft.Text("Voice Conversation", size=24, weight=ft.FontWeight.BOLD, color="#FFD700"),
        voice_dropdown,
        start_recording_btn,
        stop_recording_btn,
        recording_status,
        ft.Divider(),
        transcription_display,
        llm_response_display,
        response_player
    )


# Route to serve Flet app
@app.route('/flet')
def flet_route():
    ft.app(target=flet_app)
    return "Flet app should have launched"

# Web Interface (Day 3 Task)
@app.route('/')
def index():
    """Render the web interface with TTS and Echo Bot"""
    return render_template('index.html')

if __name__ == '__main__':
   
    get_valid_voices()
    app.run(debug=True)