import { useCallback, useEffect, useState } from 'react';
import './AppointmentStatus.css';
import { fetchPatientAppointments } from '../../services/api';
import { useAgentStore } from '../../store/agentStore';
import type { AppointmentSummary } from '../../types';

function formatSlotTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`appt-badge appt-badge--${status.toLowerCase()}`}>
      {status.replace('_', ' ')}
    </span>
  );
}

export function AppointmentStatus() {
  const { patientId, status: agentStatus } = useAgentStore();
  const [appointments, setAppointments] = useState<AppointmentSummary[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!patientId) return;
    setLoading(true);
    try {
      const data = await fetchPatientAppointments(patientId);
      setAppointments(data);
    } catch {
      // Silently fail — appointments will load on next refresh
    } finally {
      setLoading(false);
    }
  }, [patientId]);

  // Reload after agent finishes speaking (session may have changed appointments)
  useEffect(() => {
    if (agentStatus === 'ready' && patientId) {
      load();
    }
  }, [agentStatus, patientId, load]);

  return (
    <section className="appt-status" aria-label="Your appointments">
      <div className="appt-status__header">
        <span className="appt-status__title">Appointments</span>
        <button
          type="button"
          className="appt-status__refresh-btn"
          onClick={load}
          disabled={loading || !patientId}
          aria-label="Refresh appointments"
        >
          {loading ? 'Loading' : 'Refresh'}
        </button>
      </div>

      <div className="appt-status__body">
        {appointments.length === 0 ? (
          <div className="appt-status__empty">
            {loading ? 'Loading appointments...' : 'No appointments found.'}
          </div>
        ) : (
          appointments.map((appt) => (
            <div key={appt.appointment_id} className="appt-card">
              <div className="appt-card__top">
                <div>
                  <div className="appt-card__doctor">Dr. {appt.doctor_name}</div>
                  <div className="appt-card__spec">{appt.specialization}</div>
                </div>
                <StatusBadge status={appt.status} />
              </div>
              <div className="appt-card__time">{formatSlotTime(appt.time)}</div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
