# Security Policy

## What this tool does

AI Orchestrator runs **AI-generated code and test commands** and can commit to
git on your behalf. Treat all model output as untrusted input.

## Execution model

- **No shell interpolation.** Every external process is launched with
  `spawn(binary, argsArray, { shell: false })` and prompts are passed over
  **stdin**, never argv. This removes command-injection vectors.
- **Test sandbox allowlist.** Proof-of-Work tests may only invoke a fixed
  allowlist of binaries (`node`, `python3`, `grep`, etc.). Anything else is
  rejected. Generated code is written to a throwaway temp dir, never executed
  from a path the model controls.
- **Syntax checks never execute code.** We use `node --check` / `py_compile`,
  not `new Function()` or `eval`.
- **Git is opt-in and scoped.** The tool will not auto-init a repo or
  `git add -A`. It stages only files it generated, and only when git is
  explicitly enabled.
- **Secret redaction.** Logs are passed through a redactor before hitting disk.
  This is best-effort — **do not paste API keys into prompts.**

## Recommended hardening for real deployments

Run the whole tool inside a container with no network and an unprivileged user.
The allowlist reduces blast radius but is not a substitute for OS-level
sandboxing of model-suggested commands.

## Reporting

Open a private security advisory or email the maintainer. Do not file public
issues for vulnerabilities.
