/* The Learning pages - each one is a menu entry and a page key under
   Admin -> Roles (backend access_service.PAGES). */

export type LearningPageKey =
  | "training_mine"
  | "training_sessions"
  | "training_programs"
  | "training_assessments"
  | "training_people"
  | "training_facilitators"
  | "training_outcomes"
  | "training_messages"
  | "training_settings"
  | "training_audit";

export const LEARNING_PAGES: { id: LearningPageKey; label: string; blurb: string }[] = [
  { id: "training_mine", label: "My trainings", blurb: "Your registrations, open assessments, scores and messages." },
  { id: "training_sessions", label: "Sessions", blurb: "Schedule sessions, register people, take attendance and follow each session from invitation to results." },
  { id: "training_programs", label: "Programs", blurb: "Programs, their learning objectives and curriculum, and versions from draft to retired." },
  { id: "training_assessments", label: "Assessments", blurb: "Pre, post and feedback forms - paired questions measure what each session changed." },
  { id: "training_people", label: "People & places", blurb: "Training roles and their scope, participants without a login, CSV import, erasure and locations." },
  { id: "training_facilitators", label: "Facilitators", blurb: "Certifications and their status, quality reviews and each facilitator's scorecard." },
  { id: "training_outcomes", label: "Outcomes", blurb: "Knowledge, confidence and learning gain - per session and compared across programs, people and places." },
  { id: "training_messages", label: "Messages", blurb: "Automated invitations and reminders: their templates, what was sent and what failed." },
  { id: "training_settings", label: "Learning settings", blurb: "Pass mark, anonymity, assessment windows, reminders, quiet hours and the QA rubric." },
  { id: "training_audit", label: "Learning audit log", blurb: "Every configuration, people, session, template and result change - append-only." },
];
