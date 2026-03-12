const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '') + '/api';

export async function fetchNewSession(): Promise<string> {
  const res = await fetch(`${API_BASE}/session/new`);
  if (!res.ok) throw new Error('Failed to create session');
  const data = await res.json();
  return data.session_id as string;
}

export async function fetchDoctors(specialization?: string) {
  const url = specialization
    ? `${API_BASE}/doctors?specialization=${encodeURIComponent(specialization)}`
    : `${API_BASE}/doctors`;
  const res = await fetch(url);
  if (!res.ok) throw new Error('Failed to fetch doctors');
  return res.json();
}

export async function fetchPatientAppointments(patientId: string | number) {
  const res = await fetch(`${API_BASE}/appointments/${patientId}`);
  if (!res.ok) throw new Error('Failed to fetch appointments');
  const data = await res.json();
  return (data.appointments ?? []) as import('../types').AppointmentSummary[];
}

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch {
    return false;
  }
}
