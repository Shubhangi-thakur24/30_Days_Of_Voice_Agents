document.addEventListener("DOMContentLoaded", function () {
  // Elements
  const voiceSelect = document.getElementById("voice-select");
  const textInput = document.getElementById("text-input");
  const generateBtn = document.getElementById("generate-btn");
  const audioPlayer = document.getElementById("audio-player");
  const statusDiv = document.getElementById("status-message");
  const startBtn = document.getElementById("start-recording");
  const stopBtn = document.getElementById("stop-recording");
  const recordingStatus = document.getElementById("recording-status");
  const echoPlayback = document.getElementById("echo-playback");
  const transcriptionResult = document.getElementById("transcription-result");

  // Variables
  let mediaRecorder;
  let audioChunks = [];
  let currentAudioUrl = null;
  let currentSessionId = generateSessionId();
  let isWaitingForResponse = false;

  // Default voices fallback
  const DEFAULT_VOICES = [
    "en-US-Natalie",
    "en-US-Mike",
    "en-GB-Lucy",
    "hi-IN-Priya",
  ];

  // ========== Text-to-Speech Functions ==========
  async function fetchVoices() {
    try {
      showStatus("Loading available voices...", "info");

      const response = await fetch("/get_voices");

      if (!response.ok)
        throw new Error(`HTTP error! status: ${response.status}`);

      const data = await response.json();
      populateVoiceSelect(data.voices);
      clearStatus();
    } catch (error) {
      console.error("Error fetching voices:", error);
      showStatus("Error loading voices. Using default options.", "error");
      populateVoiceSelect(DEFAULT_VOICES);
    }
  }

  // Fallback audio system
  function playFallbackAudio(message) {
    // 1. Try to use browser TTS first
    if ("speechSynthesis" in window) {
      const utterance = new SpeechSynthesisUtterance(message);
      utterance.rate = 0.9;
      window.speechSynthesis.speak(utterance);
      return;
    }

    // 2. Show visual fallback
    recordingStatus.textContent = message;
    recordingStatus.style.color = "red";

    // 3. If we have a fallback URL from server
    if (window.currentFallbackUrl) {
      const audio = new Audio(window.currentFallbackUrl);
      audio.play().catch((e) => console.error("Fallback audio failed:", e));
    }
  }
  // Store the server's fallback URL when available
  let currentFallbackUrl = null;

  function populateVoiceSelect(voices) {
    voiceSelect.innerHTML = "";
    voices.forEach((voice) => {
      const option = document.createElement("option");
      option.value = voice;
      option.textContent = voice;
      voiceSelect.appendChild(option);
    });
  }

  generateBtn.addEventListener("click", async function () {
    const text = textInput.value.trim();
    const voice = voiceSelect.value;

    if (!text) {
      showStatus("Please enter some text", "error");
      return;
    }

    try {
      showStatus("Generating audio...", "info");
      generateBtn.disabled = true;

      // Clear previous audio if exists
      if (currentAudioUrl) {
        URL.revokeObjectURL(currentAudioUrl);
        currentAudioUrl = null;
      }

      const response = await fetch("/generate_audio", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, voice }),
      });

      const data = await response.json();

      if (!response.ok)
        throw new Error(data.error || "Failed to generate audio");

      if (!data.audio_url) throw new Error("No audio URL received");

      // Create object URL for playback
      currentAudioUrl = data.audio_url;
      audioPlayer.src = currentAudioUrl;
      audioPlayer.hidden = false;

      // Auto-play the audio (with user gesture)
      audioPlayer.play().catch((e) => {
        console.warn("Auto-play prevented:", e);
        showStatus("Click the play button to listen", "info");
      });

      showStatus(`Audio generated with voice: ${data.voice_used}`, "success");
    } catch (error) {
      console.error("Generation error:", error);
      showStatus(`Error: ${error.message}`, "error");

      // Special handling for voice errors
      if (error.message.includes("voice") && data?.suggestions) {
        showStatus(
          `Try one of these voices: ${data.suggestions.join(", ")}`,
          "error"
        );
      }
    } finally {
      generateBtn.disabled = false;
    }
  });

  // ========== Echo Bot Functions ==========
  function generateSessionId() {
    return "session-" + Math.random().toString(36).substring(2, 11);
  }

  // Update URL with session ID
  function updateUrlWithSession() {
    const url = new URL(window.location.href);
    url.searchParams.set("session", currentSessionId);
    window.history.pushState({}, "", url);
  }

  // Get session ID from URL or generate new
  function getSessionId() {
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get("session") || generateSessionId();
  }

  function initSession() {
    currentSessionId = getSessionId();
    updateUrlWithSession();
    console.log(`Starting/continuing session: ${currentSessionId}`);
  }

  async function initRecorder() {
    try {
      showRecordingStatus("Requesting microphone access...", "info");

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaRecorder = new MediaRecorder(stream);

      mediaRecorder.ondataavailable = (event) => {
        audioChunks.push(event.data);
      };

      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(audioChunks, { type: "audio/wav" });
        await Promise.all([
          processEchoAudio(audioBlob),
          uploadAndTranscribe(audioBlob),
        ]);
        audioChunks = [];
      };

      return true;
    } catch (error) {
      console.error("Error accessing microphone:", error);
      showRecordingStatus("Microphone access denied", "error");
      return false;
    }
  }

  async function processEchoAudio(audioBlob) {
    showRecordingStatus("Processing with Echo Bot...", "info");

    try {
      const formData = new FormData();
      formData.append("audio", audioBlob, "recording.wav");

      const response = await fetch("/tts/echo", {
        method: "POST",
        body: formData,
      });

      const data = await response.json();

      if (!response.ok) {
        // Check for fallback audio from server
        if (data.audio_url) {
          if (echoPlayback) {
            // Check if element exists
            echoPlayback.src = data.audio_url;
            echoPlayback.hidden = false;
          }
          showRecordingStatus(`Service degraded: ${data.message}`, "warning");
          return;
        }
        throw new Error(data.error || "Echo Bot processing failed");
      }

      if (!data.audio_url) throw new Error("No audio URL received");

      // Add null check before setting src
      if (echoPlayback) {
        echoPlayback.src = data.audio_url;
        echoPlayback.hidden = false;

        // Add error handling for audio playback
        echoPlayback.onerror = function () {
          showRecordingStatus("Failed to play audio response", "error");
          playFallbackAudio("I'm having trouble playing the response.");
        };
      } else {
        console.error("Echo playback audio element not found");
        playFallbackAudio("I'm having trouble responding right now.");
      }

      if (data.transcription) {
        transcriptionResult.textContent = data.transcription;
      }

      showRecordingStatus("Echo Bot complete!", "success");
    } catch (error) {
      console.error("Echo Bot error:", error);
      showRecordingStatus(`Echo Bot error: ${error.message}`, "error");
      playFallbackAudio("I'm having trouble responding right now.");
    }
  }

  async function uploadAndTranscribe(audioBlob) {
    try {
      const formData = new FormData();
      formData.append("file", audioBlob, "recording.wav");

      const response = await fetch("/transcribe/file", {
        method: "POST",
        body: formData,
      });

      const data = await response.json();

      if (data.error) throw new Error(data.error);
      if (data.transcription && !transcriptionResult.textContent) {
        transcriptionResult.textContent = data.transcription;
      }
    } catch (error) {
      console.error("Transcription error:", error);
      if (!transcriptionResult.textContent) {
        transcriptionResult.textContent = "Transcription failed";
      }
    }
  }

  // Handle recording
  // Updated recording functionality
async function toggleRecording() {
    if (isRecording) {
        await stopRecording();
    } else {
        await startRecording();
    }
}

async function startRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ 
            audio: true,
            video: false
        });
        
        mediaRecorder = new MediaRecorder(stream);
        audioChunks = [];
        
        mediaRecorder.ondataavailable = (event) => {
            audioChunks.push(event.data);
        };
        
        mediaRecorder.start();
        isRecording = true;
        updateUI();
        
    } catch (error) {
        console.error("Recording error:", error);
        
        // Specific error messages
        if (error.name === 'NotAllowedError') {
            alert("Please allow microphone access in your browser settings");
        } else if (error.name === 'NotFoundError') {
            alert("No microphone device found");
        } else {
            alert(`Recording error: ${error.message}`);
        }
    }
}

  function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      mediaRecorder.stop();
      startBtn.disabled = false;
      stopBtn.disabled = true;
      recordingStatus.textContent = "Processing...";

      // Stop all tracks
      mediaRecorder.stream.getTracks().forEach((track) => track.stop());
    }
  }

  async function processRecording() {
    if (isWaitingForResponse) return;
    isWaitingForResponse = true;

    try {
      const audioBlob = new Blob(audioChunks, { type: "audio/wav" });
      const formData = new FormData();
      formData.append("audio", audioBlob, "recording.wav");

      const response = await fetch(`/agent/chat/${currentSessionId}`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.message || "Request failed");
      }

      const data = await response.json();

      // Handle case where audio_url is not provided
      if (!data.audio_url) {
        playFallbackAudio("I didn't get a proper response.");
        throw new Error("No audio URL in response");
      }

      // Update UI and play audio
      transcriptionResult.textContent =
        data.transcription || "No transcription";
      echoPlayback.src = data.audio_url;
      echoPlayback.hidden = false;

      // Auto-start next recording
      echoPlayback.onended = () => !isWaitingForResponse && startRecording();
    } catch (err) {
      console.error("Processing error:", err);
      playFallbackAudio("I'm having trouble responding right now.");
    } finally {
      isWaitingForResponse = false;
    }
  }

  // Add to your recording start function
function showProcessingAnimation() {
  const statusDiv = document.getElementById('status');
  statusDiv.innerHTML = `
    Processing
    <span class="processing">
      <span class="processing-dot"></span>
      <span class="processing-dot"></span>
      <span class="processing-dot"></span>
    </span>
  `;
}

// Add to your message display function
function addMessage(text, isUser) {
  const messageDiv = document.createElement('div');
  messageDiv.className = 'message';
  messageDiv.classList.add(isUser ? 'user-message' : 'ai-message');
  messageDiv.textContent = text;
  
  // Add to conversation container
  const container = document.getElementById('conversation');
  container.appendChild(messageDiv);
  
  // Scroll to bottom
  container.scrollTop = container.scrollHeight;
  
  // Add audio visualizer for AI responses
  if (!isUser) {
    const visualizer = document.createElement('div');
    visualizer.className = 'audio-visualizer';
    for (let i = 0; i < 20; i++) {
      const bar = document.createElement('div');
      bar.className = 'visualizer-bar';
      bar.style.animationDelay = `${i * 0.05}s`;
      bar.style.height = `${5 + Math.random() * 20}px`;
      visualizer.appendChild(bar);
    }
    container.appendChild(visualizer);
  }
}
  // ========== Helper Functions ==========
  function showStatus(message, type = "info") {
    statusDiv.textContent = message;
    statusDiv.className = `status-${type}`;
  }

  function showRecordingStatus(message, type = "info") {
    recordingStatus.textContent = message;
    recordingStatus.className = `status-${type}`;
  }

  function clearStatus() {
    statusDiv.textContent = "";
    statusDiv.className = "";
  }

  // ========== Event Listeners ==========
  startBtn.addEventListener("click", async () => {
    const ready = await initRecorder();
    if (ready) {
      audioChunks = [];
      mediaRecorder.start();
      startBtn.disabled = true;
      stopBtn.disabled = false;
      showRecordingStatus("Recording... Speak now!", "info");
      transcriptionResult.textContent = "";
      echoPlayback.hidden = true;
    }
  });

  stopBtn.addEventListener("click", () => {
    if (mediaRecorder?.state !== "inactive") {
      mediaRecorder.stop();
      startBtn.disabled = false;
      stopBtn.disabled = true;
      mediaRecorder.stream.getTracks().forEach((track) => track.stop());
    }
  });

  // Clean up on page unload
  window.addEventListener("beforeunload", () => {
    if (currentAudioUrl) {
      URL.revokeObjectURL(currentAudioUrl);
    }
  });

  // Initialize
  fetchVoices();
  initSession();
});
