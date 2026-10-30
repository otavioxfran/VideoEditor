import subprocess
from pathlib import Path

def create_sample_video(output_mp4: str = "sample_test.mp4"):
    out_path = Path(output_mp4).resolve()
    audio_wav = out_path.parent / "temp_tts.wav"

    # PowerShell TTS script to speak phrases that trigger our rules:
    # 1. Opening
    # 2. Verbal reset ("No wait let me start again cut this")
    # 3. Retake
    # 4. Legal investigation mention
    # 5. Privacy phone number
    tts_text = (
        "Hello everyone. "
        "Não, pera, deixa eu começar de novo, corta isso. "
        "Olá a todos, bem-vindos ao canal! "
        "Segundo apuração, a empresa é investigada por suspeita de fraude. "
        "Meu telefone pessoal é 11 98888-7777."
    )

    audio_wav_escaped = str(audio_wav).replace('\\', '\\\\')
    ps_script = f"""
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $synth.SetOutputToWaveFile('{audio_wav_escaped}')
    $synth.Speak('{tts_text}')
    $synth.Dispose()
    """

    print("Generating TTS audio via Windows System.Speech...")
    subprocess.run(["powershell", "-Command", ps_script], check=True)

    # Multiplex with a synthetic 1080p video test source with timecode
    print("Generating synthetic MP4 video with FFmpeg...")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=0x181c24:s=1280x720:r=30",
        "-i", str(audio_wav),
        "-vf", "drawtext=text='VideoEditor MVP Test':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=(h-text_h)/2",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(out_path)
    ]
    subprocess.run(cmd, check=True)

    if audio_wav.exists():
        audio_wav.unlink()

    print(f"Sample video created successfully at: {out_path}")
    return out_path

if __name__ == "__main__":
    create_sample_video()
