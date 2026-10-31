// On-device speech: zero cost, works offline, no audio leaves the phone.

export function canSpeak(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

function pickVoice(): SpeechSynthesisVoice | undefined {
  const voices = window.speechSynthesis.getVoices();
  return (
    voices.find((v) => v.lang === "en-IN") ||
    voices.find((v) => v.lang.startsWith("en-GB")) ||
    voices.find((v) => v.lang.startsWith("en"))
  );
}

/** Speak a list of segments in order; onSegment fires as each one starts (for highlighting). */
export function speak(segments: string[], onSegment: (i: number) => void, onEnd: () => void): () => void {
  const synth = window.speechSynthesis;
  synth.cancel();
  let cancelled = false;
  const voice = pickVoice();
  segments.forEach((text, i) => {
    const u = new SpeechSynthesisUtterance(text);
    if (voice) u.voice = voice;
    u.rate = 1.04;
    u.pitch = 1;
    u.onstart = () => !cancelled && onSegment(i);
    if (i === segments.length - 1) u.onend = () => !cancelled && onEnd();
    synth.speak(u);
  });
  return () => {
    cancelled = true;
    synth.cancel();
  };
}

type Recognition = {
  lang: string; interimResults: boolean; maxAlternatives: number; start: () => void; stop: () => void;
  onresult: (e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void; onend: () => void; onerror: () => void;
};

export function canListen(): boolean {
  const w = window as unknown as Record<string, unknown>;
  return Boolean(w.SpeechRecognition || w.webkitSpeechRecognition);
}

export function listen(onText: (t: string) => void, onDone: () => void): () => void {
  const w = window as unknown as Record<string, new () => Recognition>;
  const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
  const rec = new Ctor();
  rec.lang = "en-IN";
  rec.interimResults = true;
  rec.maxAlternatives = 1;
  rec.onresult = (e) => {
    const parts = Array.from(e.results).map((r) => r[0].transcript);
    onText(parts.join(" "));
  };
  rec.onend = onDone;
  rec.onerror = onDone;
  rec.start();
  return () => rec.stop();
}
