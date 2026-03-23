#!/usr/bin/env python3
"""
Generate simple placeholder intro and outro jingles using ffmpeg.

These are short, pleasant tones that serve as placeholders.
Replace intro.mp3 and outro.mp3 with professional jingles later.

Usage:
    python assets/generate_jingles.py

Produces:
    assets/intro.mp3   — 6-second rising chime
    assets/outro.mp3   — 5-second fading chime
"""

import subprocess
import os

ASSETS_DIR = os.path.dirname(os.path.abspath(__file__))


def generate_intro():
    """Generate a 6-second intro jingle: rising tones with gentle fade-in."""
    output = os.path.join(ASSETS_DIR, "intro.mp3")

    # Layer multiple sine tones for a pleasant chime effect
    # C major chord progression: C4, E4, G4, C5
    filter_complex = (
        # Base tone (C4 = 261.63 Hz)
        "sine=frequency=261.63:duration=6:sample_rate=44100,volume=0.3[t1];"
        # E4 = 329.63 Hz, delayed slightly
        "sine=frequency=329.63:duration=5:sample_rate=44100,volume=0.25,adelay=500|500[t2];"
        # G4 = 392.00 Hz, delayed more
        "sine=frequency=392.00:duration=4:sample_rate=44100,volume=0.2,adelay=1000|1000[t3];"
        # C5 = 523.25 Hz, high sparkle
        "sine=frequency=523.25:duration=3:sample_rate=44100,volume=0.15,adelay=1500|1500[t4];"
        # Mix all tones
        "[t1][t2][t3][t4]amix=inputs=4:duration=longest,"
        # Apply fade in/out and normalize
        "afade=t=in:st=0:d=0.5,afade=t=out:st=4.5:d=1.5,"
        "loudnorm=I=-16:TP=-1.5:LRA=11"
    )

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=261.63:duration=6:sample_rate=44100",
        "-f", "lavfi", "-i", f"sine=frequency=329.63:duration=5:sample_rate=44100",
        "-f", "lavfi", "-i", f"sine=frequency=392.00:duration=4:sample_rate=44100",
        "-f", "lavfi", "-i", f"sine=frequency=523.25:duration=3:sample_rate=44100",
        "-filter_complex",
        "[0]volume=0.3[t1];"
        "[1]volume=0.25,adelay=500|500[t2];"
        "[2]volume=0.2,adelay=1000|1000[t3];"
        "[3]volume=0.15,adelay=1500|1500[t4];"
        "[t1][t2][t3][t4]amix=inputs=4:duration=longest,"
        "afade=t=in:st=0:d=0.5,afade=t=out:st=4.5:d=1.5,"
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-t", "6",
        "-b:a", "192k",
        output
    ]

    subprocess.run(cmd, check=True, capture_output=True)
    print(f"Created: {output}")


def generate_outro():
    """Generate a 5-second outro jingle: descending tones with fade-out."""
    output = os.path.join(ASSETS_DIR, "outro.mp3")

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=frequency=523.25:duration=5:sample_rate=44100",
        "-f", "lavfi", "-i", "sine=frequency=392.00:duration=4:sample_rate=44100",
        "-f", "lavfi", "-i", "sine=frequency=329.63:duration=3:sample_rate=44100",
        "-f", "lavfi", "-i", "sine=frequency=261.63:duration=5:sample_rate=44100",
        "-filter_complex",
        "[0]volume=0.25[t1];"
        "[1]volume=0.2,adelay=500|500[t2];"
        "[2]volume=0.2,adelay=1000|1000[t3];"
        "[3]volume=0.3,adelay=0|0[t4];"
        "[t1][t2][t3][t4]amix=inputs=4:duration=longest,"
        "afade=t=in:st=0:d=0.3,afade=t=out:st=2.5:d=2.5,"
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-t", "5",
        "-b:a", "192k",
        output
    ]

    subprocess.run(cmd, check=True, capture_output=True)
    print(f"Created: {output}")


if __name__ == "__main__":
    generate_intro()
    generate_outro()
    print("Done! Replace intro.mp3 and outro.mp3 with professional jingles when ready.")
