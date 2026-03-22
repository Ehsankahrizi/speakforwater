"""
Manages the default SpeakForWater podcast prompt.
The prompt is used as the "episode focus" in NotebookLM's Audio Overview.
"""

DEFAULT_SPEAKFORWATER_PROMPT = """Prompt (SpeakforWater – Podcast Script for One Paper)
You are creating an episode for the website SpeakforWater.
This episode is a podcast conversation between:
• Anna (journalist, friendly, curious, great at simplifying)
• Ehsan (researcher, expert, explains clearly without jargon)

Goal and audience
The audience is non-experts: farmers/agriculturists, local residents, water users, stakeholders, and people working in water-related industries. They want practical understanding but don't have time or technical background to read research papers.

Source rules (very important)
• Use only the paper(s) uploaded in this notebook as your source.
• If a detail is missing from the paper, say: "The paper does not specify."
• Do not invent numbers, locations, methods, or results.

Episode format
• Length: 6–8 minutes (roughly).
• Write as a dialogue script with speaker labels:
  ANNA: …
  EHSAN: …
• Keep language simple and practical.
• Define technical terms in one short sentence the first time they appear.

Episode structure
1. Cold open / welcome (Anna)
   - Anna must start with: "Hello, welcome back to SpeakforWater. I am Anna"
   - Then introduce herself briefly (1–2 sentences).
   - Introduce Ehsan (1–2 sentences) and must tell "Today, we are hosting Ehsan. Welcome, Ehsan".
   - Then Ehsan must introduce herself and then introduce the paper topic for today (1–2 sentences).

2. Why this paper matters (big picture)
   - Anna asks why this topic matters to everyday water users.
   - Ehsan explains the real-world problem in plain language.

3. What the researchers did (methods)
   - Ehsan explains what they did without equations.
   - Anna asks one clarifying question to keep it accessible.

4. Key findings (3–5 points)
   - Ehsan explains the key results.
   - Anna repeatedly pushes for: "What does this mean in real life?"

5. Practical takeaways
   - Give 3–5 actionable takeaways for water users (farmers/local managers/etc.).
   - If actions depend on local conditions/data, say what those conditions are.

6. Limitations & uncertainty (not generic)
   - Ehsan explains what might not generalize and what assumptions the study made.
   - Mention what extra data/experiments would strengthen confidence.

7. Wrap-up
   - Anna summarizes in 3 bullets (spoken).
   - Ehsan gives a final "If you remember one thing…" sentence.
   - Anna closes with a friendly goodbye and teaser for the next paper.

At the end Ehsan concludes the paper in 1-2 sentences and Anna appreciates Ehsan and must tell "Thanks Ehsan, and Thank you all for listening to us. See you on next episode".

Output
The total time of this podcast must be less than 10 minutes."""


def get_prompt(custom_prompt: str | None = None) -> str:
    """Return the custom prompt if provided, otherwise the default."""
    if custom_prompt and custom_prompt.strip():
        return custom_prompt.strip()
    return DEFAULT_SPEAKFORWATER_PROMPT
