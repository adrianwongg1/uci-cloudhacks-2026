import { StyleSheet, Text, View } from 'react-native';

type Props = {
  flightIata: string;
  airline: string;
  origin: string;
  destination: string;
  scheduledDeparture: string | null;
  currentStatus: string | null;
  currentDelayMinutes: number | null;
};

function formatTime(iso: string | null): string {
  if (!iso) return '--';
  const t = iso.slice(11, 16);
  return t || iso;
}

export default function FlightCard({
  flightIata,
  airline,
  origin,
  destination,
  scheduledDeparture,
  currentStatus,
  currentDelayMinutes,
}: Props) {
  return (
    <View style={styles.card}>
      <View style={styles.row}>
        <Text style={styles.flight}>{flightIata}</Text>
        <Text style={styles.airline}>{airline}</Text>
      </View>
      <View style={styles.routeRow}>
        <Text style={styles.airport}>{origin}</Text>
        <Text style={styles.arrow}>→</Text>
        <Text style={styles.airport}>{destination}</Text>
      </View>
      <View style={styles.metaRow}>
        <Text style={styles.meta}>Scheduled {formatTime(scheduledDeparture)}</Text>
        {currentStatus ? <Text style={styles.meta}>{currentStatus}</Text> : null}
        {currentDelayMinutes != null && currentDelayMinutes > 0 ? (
          <Text style={[styles.meta, styles.delay]}>+{currentDelayMinutes} min</Text>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#fff',
    borderRadius: 16,
    padding: 20,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' },
  flight: { fontSize: 22, fontWeight: '800', color: '#0f172a' },
  airline: { fontSize: 14, color: '#64748b' },
  routeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 12,
  },
  airport: { fontSize: 32, fontWeight: '700', color: '#0f172a' },
  arrow: { fontSize: 24, color: '#94a3b8' },
  metaRow: { flexDirection: 'row', gap: 12, marginTop: 12, flexWrap: 'wrap' },
  meta: { fontSize: 13, color: '#475569' },
  delay: { color: '#b91c1c', fontWeight: '700' },
});
