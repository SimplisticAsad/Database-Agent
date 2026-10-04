OUTPUT RULES (strict)
- Respond with exactly ONE JSON object and nothing else: no prose before or after, no Markdown, no code fences.
- The object must validate against the JSON Schema given in OUTPUT CONTRACT. Do not add keys the schema does not define.
- SQL goes in JSON arrays of strings, ONE COMPLETE statement per array item, each ending in a semicolon.
  Escape double quotes and newlines properly so the JSON is valid. A function body may span many lines in one item.
- Do not include the schema/contract itself in your answer.
