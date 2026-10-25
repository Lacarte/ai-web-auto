# <App> — decision rules

The "brain": when each action may run, what must be true first, and what counts as done.
The code must enforce every rule written here; change both together.

## Identity
How an item (row, project, file...) is identified reliably - prefer a stable id or URL over list position.

## Preconditions and safety gates
- Which actions are sensitive (spend money, delete, send, publish) and what must be verified before them.
- Which actions must never be retried automatically.

## When an action counts as done
The observable evidence for each action (see actions.json "evidence").
