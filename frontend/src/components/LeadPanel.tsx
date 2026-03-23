import type { LeadData } from '../hooks/useVoiceClient';
import {
  User,
  Building2,
  Mail,
  Phone,
  Globe,
  Briefcase,
  DollarSign,
  Clock,
  Package,
  CheckCircle,
} from 'lucide-react';

export const LeadPanel: React.FC<{ data: LeadData }> = ({ data }) => {
  const pct = Math.round((data.qualification_score ?? 0) * 100);
  const temp = data.lead_temperature ?? 'unknown';
  const isSaved = data.lead_saved;

  const fields: { icon: React.ReactNode; label: string; value?: string }[] = [
    { icon: <User size={13} />, label: 'Name', value: data.lead_name },
    { icon: <Building2 size={13} />, label: 'Company', value: data.lead_company },
    { icon: <Mail size={13} />, label: 'Email', value: data.lead_email },
    { icon: <Phone size={13} />, label: 'Phone', value: data.lead_phone },
    { icon: <Globe size={13} />, label: 'Country', value: data.lead_country },
    { icon: <Briefcase size={13} />, label: 'Industry', value: data.lead_industry },
    { icon: <Package size={13} />, label: 'Service', value: data.lead_service },
    { icon: <DollarSign size={13} />, label: 'Budget', value: data.lead_budget },
    { icon: <Clock size={13} />, label: 'Timeline', value: data.lead_timeline },
    { icon: <Package size={13} />, label: 'Package', value: data.lead_package },
  ];

  const tempColor =
    temp === 'hot'
      ? { bg: 'rgba(239, 68, 68, 0.15)', text: '#FCA5A5' }
      : temp === 'warm'
        ? { bg: 'rgba(254, 202, 87, 0.15)', text: '#FCD34D' }
        : { bg: 'rgba(59, 130, 246, 0.15)', text: '#93C5FD' };

  const scoreColor =
    pct >= 65 ? 'var(--accent-green)' : pct >= 35 ? 'var(--accent-alt)' : 'var(--accent-blue)';

  return (
    <div
      style={{
        width: '280px',
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-lg)',
        padding: '20px',
        display: 'flex',
        flexDirection: 'column',
        gap: '20px',
        overflowY: 'auto',
        maxHeight: '100%',
      }}
    >
      <h3
        style={{
          fontSize: '16px',
          margin: 0,
          borderBottom: '1px solid var(--border)',
          paddingBottom: '10px',
        }}
      >
        Lead Qualification
      </h3>

      {/* Score + Temperature */}
      <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
        {/* Score ring */}
        <div
          style={{
            width: 56,
            height: 56,
            borderRadius: '50%',
            background: `conic-gradient(${scoreColor} ${pct}%, var(--border) 0)`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          <div
            style={{
              width: 46,
              height: 46,
              borderRadius: '50%',
              background: 'var(--surface)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <span style={{ fontSize: '14px', fontWeight: 'bold' }}>{pct}%</span>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <span
            style={{
              display: 'inline-block',
              padding: '3px 10px',
              borderRadius: '12px',
              fontSize: '11px',
              fontWeight: 700,
              background: tempColor.bg,
              color: tempColor.text,
              textTransform: 'uppercase',
              letterSpacing: '0.5px',
            }}
          >
            {temp}
          </span>
          <span
            style={{
              fontSize: '11px',
              color: 'var(--text-muted)',
              textTransform: 'uppercase',
              letterSpacing: '0.5px',
            }}
          >
            {(data.agent_state ?? 'idle').replace(/_/g, ' ')}
          </span>
        </div>
      </div>

      {isSaved && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 12px',
            background: 'rgba(16, 185, 129, 0.1)',
            border: '1px solid rgba(16, 185, 129, 0.3)',
            borderRadius: 'var(--radius-sm)',
            fontSize: '12px',
            color: 'var(--accent-green)',
            fontWeight: 600,
          }}
        >
          <CheckCircle size={14} /> Lead saved to Excel
        </div>
      )}

      {/* Extracted fields */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
        }}
      >
        <span
          style={{
            fontSize: '11px',
            color: 'var(--text-muted)',
            textTransform: 'uppercase',
            letterSpacing: '0.5px',
            fontWeight: 600,
          }}
        >
          Extracted Data
        </span>
        {fields.map((f, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              fontSize: '12px',
              padding: '6px 8px',
              background: f.value ? 'var(--surface-hover)' : 'transparent',
              borderRadius: 'var(--radius-sm)',
              color: f.value ? 'var(--text-heading)' : 'var(--text-muted)',
              opacity: f.value ? 1 : 0.4,
            }}
          >
            <span style={{ flexShrink: 0, opacity: 0.6 }}>{f.icon}</span>
            <span style={{ fontWeight: 500, minWidth: '55px' }}>{f.label}</span>
            <span
              style={{
                flex: 1,
                textAlign: 'right',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {f.value || '—'}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};
