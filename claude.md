⚡ CLAUDE CODE — ULTRA TOKEN SAVER MODE
CORE RULES

* OUTPUT ONLY TASK EXECUTION
* NO EXPLANATION
* NO SUMMARIES
* NO REPEATING CONTEXT
* NO REWRITING UNCHANGED CODE
* NO EXTRA FILES
* NO REDESIGN
* NO OVERENGINEERING
* MIN TOKENS ALWAYS
RESPONSE RULE
DEFAULT:

* return PATCH only
* shortest valid answer NEVER:
* explain obvious code
* restate prompt
* describe architecture unless asked
EXECUTION PRIORITY

1. identify exact target
2. minimal analysis
3. minimal patch
4. stop DO NOT explore unrelated files.
FILE ACCESS RULE
READ ONLY:

* directly related files
* imported dependency files if required DO NOT scan entire project.
MODES
DEBUG

* root cause first
* minimal fix only
FEATURE

* additive only
* preserve architecture
REFACTOR

* structure cleanup only
* no behavior change
FRONTEND

* UI only
BACKEND

* backend only
OUTPUT FORMAT
[CHANGED FILES]

* path [PATCH] <only changed code>
CONTEXT RULES

* forget unused context
* avoid long memory chains
* avoid repeating previous outputs
* use shortest reasoning possible
TOKEN CONTROL

* prefer bullets over paragraphs
* prefer patch over full file
* prefer exact file paths
* avoid broad prompts
* avoid multi-task prompts
* one task per session
TASK FORMAT
@skill-name [MODE] DEBUG | FEATURE | REFACTOR | FRONTEND | BACKEND [GOAL] single objective [TARGET FILES] exact paths [CONTEXT] minimal required info only [TASK] exact modification [CONSTRAINTS]

* no break existing
* minimal change
* production safe [OUTPUT] PATCH only
SKILL PATH
C:\Users\Aswin-pc\.claude\skills Use: @<skill-name>
HIGH PRIORITY SKILLS
debugging-strategies security-auditor frontend-design api-design-principles test-driven-development lint-and-validate performance-optimizer root-cause-analysis
TOKEN SAVING BEHAVIOR

* do not reopen same files repeatedly
* do not explain generated code
* do not generate alternatives
* do not generate optional improvements
* stop immediately after task complete
SAFETY

* preserve APIs
* preserve DTO contracts
* preserve event flow
* preserve microservice boundaries
* no schema changes unless asked
FINAL RULE
ONLY DO REQUESTED TASK. STOP AFTER COMPLETION.
