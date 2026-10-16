import AlertsPanel from '../AlertsPanel/AlertsPanel.jsx'
import WhatsAppAlerts from '../AlertsPanel/WhatsAppAlerts.jsx'
import SubscriberAlerts from '../PublicAlerts/SubscriberAlerts.jsx'
import PageHeader, { Section } from '../Shell/PageHeader.jsx'
import InfoTip from '../ui/InfoTip.jsx'
import shell from '../Shell/Shell.module.css'

// Public alerts (DECISIONS D-44..D-47). Four different things, kept apart on purpose:
//   1. FloodNet advisories to people who subscribed (review and approval before anything is sent)
//   2. advisory drafts from the forecast (CAP-style, status Draft)
//   3. official warnings from SACHET (NDMA), shown as received, never edited
//   4. phone TEST alerts to the team's own numbers
// FloodNet is not an authorised alerting authority: nothing here is an official warning.

export default function AlertsPage({ status, statusText }) {
  return (
    <>
      <PageHeader pageKey="alerts" status={status} statusText={statusText} />
      <p className={shell.important} role="note">
        <strong>FloodNet advisories are not official warnings.</strong>
        <InfoTip label="Who issues official warnings">
          Official warnings in India are issued by authorised agencies (IMD, NDMA and state disaster management
          authorities) through SACHET. FloodNet only sends its own advisories to people who subscribed, after a person
          reviews them.
        </InfoTip>
      </p>
      <SubscriberAlerts />
      <Section title="Forecast advisory drafts" tour="panel-alerts-section">
        <AlertsPanel />
      </Section>
      <Section title="Phone alerts (TEST only)" advanced>
        <WhatsAppAlerts />
      </Section>
    </>
  )
}
