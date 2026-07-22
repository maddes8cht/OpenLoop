# Passing Long Prompts to `opencode run`

## Problem

`opencode run [message..]` accepts the prompt as a positional **message** argument.
For short prompts this works fine:

```
opencode run "Generate tests for module X"
```

But long prompts (system prompt + workflow state JSON) easily exceed the OS
command-line length limit (32 767 characters on Windows) and get silently
truncated.

## Attempted Solutions That Don't Work

| Pattern | Result |
|---|---|
| `opencode run --file prompt.md` | Fails: *"You must provide a message or a command"* — `--file` alone is not enough |
| `opencode run --file prompt.md "Follow instructions"` | Fails: *"File not found: Follow instructions"* — the positional message is consumed by `--file`'s array and treated as another file path |

**Root cause:** `--file` is declared as an `[array]` type in opencode's CLI.
Array-type options greedily consume **all following arguments** as array
elements until another flag (starting with `--`) is encountered. A positional
message that comes directly after `--file <path>` becomes part of that array
and is interpreted as a file path.

## Working Pattern

Insert a **flag that takes a value** between `--file` and the message to
terminate the `--file` array:

```
opencode run --file <prompt-file> --dir <workdir> "Follow the instructions in the attached file exactly."
```

| Part | Purpose |
|---|---|
| `--file <prompt-file>` | Attach the file containing the full prompt |
| `--dir <workdir>` | Terminates the `--file` array; also tells opencode which directory to operate in |
| `"message"` | Positional text argument — now correctly treated as message, not as file path |

Any single-value flag works as the separator (e.g. `--model`, `--title`).
`--dir` is the recommended choice because it is always meaningful.

## Implementation (OpenLoop)

In `core/runner.py` the `OpenCodeRunner.run` method constructs the command as:

```python
cmd = [self.binary, "run"]
# ... opts, -c, etc ...
cmd += ["--file", str(prompt_file.resolve())]
cmd += ["--dir", str(Path(cwd).resolve()) if cwd else "."]
cmd += ["Follow the instructions in the attached file exactly."]
```

The prompt is written to `current_prompt.md` in the configured `log_dir`
before the command is launched.
