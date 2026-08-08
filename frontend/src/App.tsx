import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";
import { Spinner } from "./components/ui";
import { HOME_BY_ROLE, useAuth, type Role } from "./lib/auth";

import Confidential from "./pages/Confidential";
import { Profile, Settings } from "./pages/Account";
import Login from "./pages/Login";

import PatientDashboard from "./pages/patient/Dashboard";
import SymptomCheck from "./pages/patient/SymptomCheck";
import Assistant from "./pages/patient/Assistant";
import FindCare from "./pages/patient/FindCare";
import Programmes from "./pages/patient/Programmes";
import { Imaging, Reports } from "./pages/patient/Records";
import {
  Appointments,
  History,
  Notifications,
  Sharing,
} from "./pages/patient/Misc";

import { Alerts, DependentDetail, Dependents } from "./pages/guardian/Guardian";

import {
  DoctorAppointments,
  DoctorDashboard,
  MyPatients,
  PatientDetail,
  Queue,
} from "./pages/doctor/Doctor";

import {
  Capacity,
  Forecast,
  HospitalDashboard,
  NoShow,
  Providers,
} from "./pages/hospital/Hospital";

import {
  AdminAi,
  AdminAudit,
  AdminFacilities,
  AdminOverview,
  AdminProviders,
  AdminUsers,
} from "./pages/admin/Admin";

/** Gate a subtree on authentication and (optionally) an allowed role set. */
function Protected({ roles }: { roles?: Role[] }) {
  const { user, loading } = useAuth();

  if (loading) return <Spinner label="Loading SuwaPath…" />;
  if (!user) return <Navigate to="/login" replace />;
  if (roles && !roles.includes(user.role)) {
    return <Navigate to={HOME_BY_ROLE[user.role]} replace />;
  }
  return <AppShell />;
}

function RoleHome() {
  const { user, loading } = useAuth();
  if (loading) return <Spinner label="Loading SuwaPath…" />;
  return <Navigate to={user ? HOME_BY_ROLE[user.role] : "/login"} replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/private" element={<Confidential />} />

      {/* Patient */}
      <Route element={<Protected roles={["patient"]} />}>
        <Route path="/patient" element={<PatientDashboard />} />
        <Route path="/patient/symptom-check" element={<SymptomCheck />} />
        <Route path="/patient/assistant" element={<Assistant />} />
        <Route path="/patient/find-care" element={<FindCare />} />
        <Route path="/patient/appointments" element={<Appointments />} />
        <Route path="/patient/reports" element={<Reports />} />
        <Route path="/patient/imaging" element={<Imaging />} />
        <Route path="/patient/programmes" element={<Programmes />} />
        <Route path="/patient/history" element={<History />} />
        <Route path="/patient/sharing" element={<Sharing />} />
        <Route path="/patient/notifications" element={<Notifications />} />
        <Route path="/patient/profile" element={<Profile />} />
        <Route path="/patient/settings" element={<Settings />} />
      </Route>

      {/* Guardian */}
      <Route element={<Protected roles={["guardian"]} />}>
        <Route path="/guardian" element={<Dependents />} />
        <Route path="/guardian/dependents/:patientId" element={<DependentDetail />} />
        <Route path="/guardian/alerts" element={<Alerts />} />
        <Route path="/guardian/notifications" element={<Notifications />} />
        <Route path="/guardian/profile" element={<Profile />} />
        <Route path="/guardian/settings" element={<Settings />} />
      </Route>

      {/* Doctor */}
      <Route element={<Protected roles={["doctor"]} />}>
        <Route path="/doctor" element={<DoctorDashboard />} />
        <Route path="/doctor/queue" element={<Queue />} />
        <Route path="/doctor/appointments" element={<DoctorAppointments />} />
        <Route path="/doctor/patients" element={<MyPatients />} />
        <Route path="/doctor/patients/:patientId" element={<PatientDetail />} />
        <Route path="/doctor/notifications" element={<Notifications />} />
        <Route path="/doctor/profile" element={<Profile />} />
        <Route path="/doctor/settings" element={<Settings />} />
      </Route>

      {/* Hospital administrator */}
      <Route element={<Protected roles={["hospital_admin", "system_admin"]} />}>
        <Route path="/hospital" element={<HospitalDashboard />} />
        <Route path="/hospital/forecast" element={<Forecast />} />
        <Route path="/hospital/no-show" element={<NoShow />} />
        <Route path="/hospital/capacity" element={<Capacity />} />
        <Route path="/hospital/providers" element={<Providers />} />
        <Route path="/hospital/notifications" element={<Notifications />} />
        <Route path="/hospital/profile" element={<Profile />} />
        <Route path="/hospital/settings" element={<Settings />} />
      </Route>

      {/* System administrator */}
      <Route element={<Protected roles={["system_admin"]} />}>
        <Route path="/admin" element={<AdminOverview />} />
        <Route path="/admin/users" element={<AdminUsers />} />
        <Route path="/admin/providers" element={<AdminProviders />} />
        <Route path="/admin/facilities" element={<AdminFacilities />} />
        <Route path="/admin/ai" element={<AdminAi />} />
        <Route path="/admin/audit" element={<AdminAudit />} />
        <Route path="/admin/notifications" element={<Notifications />} />
        <Route path="/admin/profile" element={<Profile />} />
        <Route path="/admin/settings" element={<Settings />} />
      </Route>

      <Route path="/" element={<RoleHome />} />
      <Route path="*" element={<RoleHome />} />
    </Routes>
  );
}
