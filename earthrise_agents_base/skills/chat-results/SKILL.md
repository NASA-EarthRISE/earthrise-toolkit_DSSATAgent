---
name: chat-results
description: >-
  Result synthesis skill composed at runtime with agent-specific formatting instructions.
---

## Results

Synthesize these results into a clear response for the user.

User's question: {user_query}

Parameters used: {params}

Results:
{results}

Formatting guidelines from agents:
{agent_results}

Instructions:
- Directly answer the user's question using the results data above.
- ONLY reference values and labels that appear in the results data. Do NOT guess, invent, or reinterpret any values.
- Use markdown formatting for clarity.
- Follow the agent-specific formatting guidelines above.
- If some tasks failed, acknowledge what's missing.
- Be concise — detailed tables are already shown to the user separately.
Do NOT wrap your response in quotes.

## Knowledge Response

Synthesize an answer from the retrieved documentation.

User question: {user_query}

Retrieved context:
{knowledge_context}

Provide a clear, concise answer. Cite sources when available.
Do NOT wrap your response in quotes.
If the context doesn't fully answer the question, say so.
