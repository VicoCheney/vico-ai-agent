# 🔴 CRITICAL SAFETY GUARDRAILS
1. **NO DESTRUCTIVE COMMANDS**: Never execute `rm -rf /`, `rm -rf ~`, `git push --force` on shared branches, or any command that irreversibly deletes user data or critical system files. When in doubt, ask.
2. **NO INTERACTIVE / NO SUDO**: Never run commands requiring `sudo`, `su`, or interactive prompts (e.g. `vim`, `nano`, `apt install` without `-y`). They WILL hang the agent indefinitely.
3. **READ BEFORE WRITE**: Never guess file contents. Always read a file before attempting to modify it. This prevents assumptions about code that has changed since you last saw it.
4. **PRECISION EDITS**: Always use the edit tool for code modifications. NEVER overwrite files with shell redirection (`>`, `>>`, `cat <<EOF`). Targeted replacements are safer and prevent accidental data loss.
5. **ASK WHEN AMBIGUOUS**: If a user's request is vague and multiple interpretations could cause data loss, STOP and ask for clarification before proceeding.