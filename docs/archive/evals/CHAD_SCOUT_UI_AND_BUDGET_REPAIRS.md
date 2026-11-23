# Scout visibility and specialist budgets — 2026-10-19

The GUI showed a completed Scout dispatch containing only its prompt. The planner
completed a placeholder dispatch before Scout ran, and Scout's real root event
inherited its own ID as its parent.

Scout now owns one dispatch lifecycle using the planner tool-call ID. Its root has
no parent; child tools and available reasoning events inherit that root. Runtime
metadata and lifecycle events share call IDs. Completion carries the gather report,
rendered in the expanded panel. No-tools failures also emit an explicit completion.
Models without a reasoning channel still expose tools and their final report.

Specialist tool-round defaults are 30 generally, 40 for UI automation, and 60 for
browser automation. Browser's task action budget is also 60. Child graph recursion
allows at least twice the tool rounds plus four, preserving larger explicit settings.
Browser fallback reads the correct tools.browser_max_actions_per_task key.

Validation: 74 backend tests and six frontend tests passed, including lifecycle
parentage, report rendering, budget configuration, handoff recursion, and a simulated
UI task completing after 12 tool rounds. TypeScript and frontend production build
passed. No live replay or manual Brain restart was performed. Production 8767 remains
outside the behavioral test scope.

Next user-driven run: inspect expanded Scout activity/report and actual specialist
termination reasons. Keep private contacts and meeting details out of tracked evidence.
