import { useState } from "react";
import Icon from "./Icon";

const DEMO_ACCOUNTS = [
  { email: "patient@suwapath.lk", name: "Nimali Fernando", role: "Patient" },
  { email: "maternal@suwapath.lk", name: "Dilini Fernando", role: "Patient · Maternal care" },
  { email: "elderly@suwapath.lk", name: "Sunil Fernando", role: "Patient · Elderly care" },
  { email: "guardian@suwapath.lk", name: "Nimal Fernando", role: "Guardian" },
  { email: "doctor@suwapath.lk", name: "Dr. Dileepa Perera", role: "Doctor · Endocrinology" },
  { email: "hospital@suwapath.lk", name: "Chathurika Bandara", role: "Hospital Admin" },
  { email: "admin@suwapath.lk", name: "Ravindu Wickramasinghe", role: "System Admin" },
];

export default function DevLoginTools({ onLogin }: { onLogin: (email: string) => void }) {
  const [open, setOpen] = useState(false);

  // Render nothing in production
  if (!import.meta.env.DEV) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50">
      {open && (
        <div className="absolute bottom-12 right-0 w-80 bg-white border border-brand-200 rounded-xl shadow-xl overflow-hidden mb-2">
          <div className="bg-brand-50 px-4 py-3 border-b border-brand-100 flex justify-between items-center">
            <h3 className="font-semibold text-brand-900 text-sm flex items-center gap-2">
              <Icon name="settings" size={16} /> Dev Login
            </h3>
            <span className="text-[10px] uppercase font-bold tracking-wider text-brand-600 bg-brand-200 px-1.5 py-0.5 rounded">Local Only</span>
          </div>
          <div className="p-2 space-y-1 max-h-[60vh] overflow-y-auto">
            {DEMO_ACCOUNTS.map((acc) => (
              <button
                key={acc.email}
                className="w-full text-left p-3 rounded-lg hover:bg-brand-50 transition flex items-center justify-between group"
                onClick={() => {
                  onLogin(acc.email);
                  setOpen(false);
                }}
              >
                <div>
                  <p className="text-sm font-medium text-ink-900">{acc.name}</p>
                  <p className="text-xs text-ink-500">{acc.role}</p>
                </div>
                <Icon name="arrowRight" size={18} className="text-brand-500 opacity-0 group-hover:opacity-100 transition-opacity translate-x-[-10px] group-hover:translate-x-0 duration-300" />
              </button>
            ))}
          </div>
        </div>
      )}
      <button
        onClick={() => setOpen(!open)}
        className={`h-12 w-12 bg-brand-600 text-white rounded-full flex items-center justify-center shadow-lg hover:bg-brand-700 transition-transform duration-300 ${open ? "rotate-90 bg-brand-800" : ""}`}
        aria-label="Toggle Dev Tools"
      >
        <Icon name={open ? "close" : "settings"} size={24} />
      </button>
    </div>
  );
}
