// The microphone side of call.js: 16 kHz, signed 16-bit little-endian mono.
class CallMic extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / 16000;
    this.pos = 0;
    this.buf = new Int16Array(1600);
    this.n = 0;
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    while (this.pos < channel.length) {
      const index = Math.floor(this.pos), fraction = this.pos - index;
      const a = channel[index], b = index + 1 < channel.length ? channel[index + 1] : a;
      const value = Math.max(-1, Math.min(1, a + (b - a) * fraction));
      this.buf[this.n++] = value < 0 ? value * 0x8000 : value * 0x7fff;
      if (this.n === this.buf.length) {
        this.port.postMessage(this.buf.buffer, [this.buf.buffer]);
        this.buf = new Int16Array(1600);
        this.n = 0;
      }
      this.pos += this.step;
    }
    this.pos -= channel.length;
    return true;
  }
}
registerProcessor('call-mic', CallMic);
