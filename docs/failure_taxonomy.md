# Failure Taxonomy

## non_ready_action

Definition: The agent proposed an action before the UI was ready.
Signals: loading text, progress bars, empty XML, disabled target, `clickable=false`.
Review: automatic for strong XML/text signals; human review for ambiguous states.
Example: tapping a button while a progress bar is still visible.

## loading_loop

Definition: The same loading or non-ready state repeats across several steps.
Signals: repeated UI hash, repeated loading text, no progress after waits.
Review: automatic detection, human review for root cause.
Example: the app keeps showing "please wait" with the same UI hash.

## modal_blocker

Definition: A modal, dialog, permission sheet, or overlay blocks normal action.
Signals: XML/text contains dialog, alert, permission, modal, popup, bottom sheet.
Review: automatic label, human review before risky dismissals.
Example: a permission dialog covers the intended target.

## stuck_loop

Definition: The agent repeats the same action without observable progress.
Signals: same action fingerprint, unchanged UI/XML/screenshot hash.
Review: automatic detection.
Example: tapping the same button three times while the screen does not change.

## false_success

Definition: The agent claims completion without observable goal evidence.
Signals: `done` action, unchanged UI, no explicit goal marker present.
Review: human review recommended for semantic task success.
Example: agent says done while still on the wrong screen.

## blank_webview

Definition: XML exposes a WebView-like shell but little useful page content.
Signals: WebView text/class with very few visible nodes.
Review: automatic as uncertain; optional future VLM evidence can help.
Example: browser chrome is visible but page content is inaccessible in XML.

## unsafe_action

Definition: The proposed action appears irreversible or high-risk.
Signals: generic keywords such as delete, reset, uninstall, pay, purchase, submit, send, confirm.
Review: human handoff recommended unless explicit confirmation context exists.
Example: tapping "Delete account" without a user confirmation context.

## no_progress

Definition: An action executed or was simulated, but no observable UI progress followed.
Signals: unchanged UI hash, XML hash, and screenshot hash.
Review: automatic detection; human review may be needed to judge semantic progress.
Example: tapping a non-responsive control.

## target_missing

Definition: The agent proposed a named UI target that is not present in the current actionable UI facts.
Signals: a proposed tap/click target is absent from generic `possible_targets`/XML facts.
Review: automatic for structured UI facts; human review recommended when the source UI facts are sparse.
Example: tapping "Save" while the current screen exposes only search/home targets.

## partial_completion

Definition: Some progress occurred, but the task is not proven complete.
Signals: UI changed but no final success marker is available.
Review: human or benchmark review recommended.
Example: search results opened, but the target result was not selected.

## wrong_screen

Definition: The agent is acting on a screen that does not match the task context.
Signals: explicit metadata or benchmark label; weak generic detection in v0.1.
Review: human or benchmark review recommended.
Example: agent tries to compose an email while still on a settings page.
