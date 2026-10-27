export const MODULE_OPTIONS = [
  { key: 'telecom_crm', label: 'Telecom CRM', description: 'Customers, sales, orders, billing and field service' },
  { key: 'training_management', label: 'Training & Learning', description: 'Programs, sessions, assessments and feedback' },
  { key: 'flow_studio', label: 'Flow Studio', description: 'Workflow authoring' },
  { key: 'page_designer', label: 'Page Designer', description: 'Forms, layouts and validation' },
  { key: 'custom_development', label: 'Custom Development', description: 'Custom intents, fields and process building' },
];
export const LEGACY_MODULES = ['telecom_crm', 'flow_studio', 'page_designer', 'custom_development'];
export default function ModuleSelector({ value, onChange }: { value: string[]; onChange: (value: string[]) => void }) {
  return <fieldset className="space-y-2 rounded-lg border border-slate-700 p-4"><legend className="px-2 text-sm font-semibold text-slate-200">Licensed modules</legend>
    <p className="text-xs text-slate-400">Select functional modules and optional authoring tools. Modules can also be activated later. Removing access preserves stored data.</p>
    <div className="grid gap-3 sm:grid-cols-2">{MODULE_OPTIONS.map(m => <label key={m.key} className="flex items-start gap-2 text-sm text-slate-200"><input type="checkbox" checked={value.includes(m.key)} onChange={e => onChange(e.target.checked ? [...value, m.key] : value.filter(x => x !== m.key))} /><span>{m.label}<small className="block text-slate-400">{m.description}</small></span></label>)}</div>
  </fieldset>;
}
