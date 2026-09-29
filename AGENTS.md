# User model preference

The user authorizes choosing the appropriate model and reasoning effort for
each request without asking again. Assess complexity before substantial work:

- Prefer GPT-5.6 Luna Medium for routine questions, log reviews, and scoped edits.
- Prefer GPT-5.6 Luna High for harder bounded fixes and autonomous gameplay.
- Reserve GPT-6 Astra High for complex architecture, difficult debugging, or
  changes requiring deep cross-system reasoning.

Apply this preference when a supported model-selection control is available.
Do not claim that an instruction or file edit changes the active model. If the
current task cannot switch models, explain that limitation accurately. Do not
start extra tasks or agents solely to work around model selection. This approval
does not authorize starting gameplay or altering unrelated settings.
