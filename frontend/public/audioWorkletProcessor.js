/**
 * audioWorkletProcessor.js — PCM audio processor for streaming STT.
 *
 * Runs in AudioWorklet thread (off main thread, no jank).
 * Receives raw float32 audio from AudioContext, converts to int16 PCM,
 * and posts to main thread for WebSocket streaming to Deepgram.
 *
 * Audio format output: Linear PCM, 16-bit signed, little-endian, mono, 16kHz.
 * Frame size: 20ms = 320 samples = 640 bytes per frame.
 *
 * Control messages from main thread:
 *   { type: 'start' }  — begin capturing and sending frames
 *   { type: 'stop' }   — stop capturing (frames are discarded)
 */

class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this._active = false
    this._buffer = new Float32Array(0)

    // Target: 320 samples per frame (20ms at 16kHz)
    // AudioWorklet delivers 128 samples per process() call at context sampleRate.
    // We accumulate and resample to 16kHz.
    this._targetSampleRate = 16000
    this._frameSize = 320  // 20ms at 16kHz

    this.port.onmessage = (e) => {
      if (e.data.type === 'start') {
        this._active = true
        this._buffer = new Float32Array(0)
      } else if (e.data.type === 'stop') {
        this._active = false
        this._buffer = new Float32Array(0)
      }
    }
  }

  process(inputs, outputs, parameters) {
    if (!this._active) return true

    const input = inputs[0]
    if (!input || !input[0] || input[0].length === 0) return true

    const channelData = input[0]  // mono channel (float32, -1 to +1)
    const inputSampleRate = sampleRate  // AudioContext sample rate (usually 44100 or 48000)

    // Resample to 16kHz
    const ratio = this._targetSampleRate / inputSampleRate
    const resampledLength = Math.round(channelData.length * ratio)
    const resampled = new Float32Array(resampledLength)

    for (let i = 0; i < resampledLength; i++) {
      const srcIndex = i / ratio
      const srcFloor = Math.floor(srcIndex)
      const srcCeil = Math.min(srcFloor + 1, channelData.length - 1)
      const frac = srcIndex - srcFloor
      resampled[i] = channelData[srcFloor] * (1 - frac) + channelData[srcCeil] * frac
    }

    // Append to buffer
    const newBuffer = new Float32Array(this._buffer.length + resampled.length)
    newBuffer.set(this._buffer)
    newBuffer.set(resampled, this._buffer.length)
    this._buffer = newBuffer

    // Emit full frames (320 samples = 20ms)
    while (this._buffer.length >= this._frameSize) {
      const frame = this._buffer.slice(0, this._frameSize)
      this._buffer = this._buffer.slice(this._frameSize)

      // Convert float32 (-1..+1) to int16 (-32768..+32767)
      const pcm16 = new Int16Array(frame.length)
      for (let i = 0; i < frame.length; i++) {
        const s = Math.max(-1, Math.min(1, frame[i]))
        pcm16[i] = s < 0 ? s * 32768 : s * 32767
      }

      // Post PCM frame to main thread (typed message for receiver check)
      this.port.postMessage({ type: 'pcm', buffer: pcm16.buffer }, [pcm16.buffer])
    }

    return true
  }
}

registerProcessor('pcm-processor', PCMProcessor)
