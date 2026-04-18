import { StackScreenProps } from '@react-navigation/stack';
import { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { RootStackParamList } from '../../App';
import { usePushToken } from '../../App';
import { subscribe } from '../api/subscribe';
import ExplanationCard from '../components/ExplanationCard';
import FlightCard from '../components/FlightCard';
import RiskBadge from '../components/RiskBadge';

type Props = StackScreenProps<RootStackParamList, 'Result'>;

function extractTime(iso: string | null): string {
  if (!iso) return '00:00';
  const m = /T(\d{2}:\d{2})/.exec(iso);
  return m ? m[1] : '00:00';
}

export default function ResultScreen({ route }: Props) {
  const { prediction, flightDate } = route.params;
  const pushToken = usePushToken();
  const [loading, setLoading] = useState(false);
  const [subscribed, setSubscribed] = useState(false);

  async function onSubscribe() {
    if (!pushToken) {
      Alert.alert(
        'Notifications not enabled',
        'Please allow notifications in your device settings to get delay alerts.'
      );
      return;
    }
    try {
      setLoading(true);
      await subscribe({
        push_token: pushToken,
        flight_iata: prediction.flight_iata,
        flight_date: flightDate,
        origin: prediction.origin,
        destination: prediction.destination,
        scheduled_departure: extractTime(prediction.scheduled_departure),
        predicted_risk: prediction.predicted_probability,
      });
      setSubscribed(true);
    } catch (e: unknown) {
      Alert.alert('Subscribe failed', e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.scroll}
      keyboardShouldPersistTaps="handled"
    >
      <FlightCard
        flightIata={prediction.flight_iata}
        airline={prediction.airline}
        origin={prediction.origin}
        destination={prediction.destination}
        scheduledDeparture={prediction.scheduled_departure}
        currentStatus={prediction.current_status}
        currentDelayMinutes={prediction.current_delay_minutes}
      />

      <View style={styles.spacer} />
      <RiskBadge level={prediction.risk_level} probability={prediction.predicted_probability} />
      <View style={styles.spacer} />
      <ExplanationCard explanation={prediction.explanation} />
      <View style={styles.spacer} />

      {subscribed ? (
        <View style={styles.confirmed}>
          <Text style={styles.confirmedTitle}>You're subscribed ✅</Text>
          <Text style={styles.confirmedBody}>
            We'll push-notify you every time this flight's delay status changes until it lands.
          </Text>
        </View>
      ) : (
        <View style={styles.subscribeCard}>
          <Text style={styles.subscribeTitle}>Get live delay alerts</Text>
          <Text style={styles.subscribeBody}>
            We'll push-notify you every time this flight's status changes, all the way through landing.
          </Text>
          <Pressable
            style={[styles.button, (loading || !pushToken) && styles.buttonDisabled]}
            onPress={onSubscribe}
            disabled={loading || !pushToken}
          >
            {loading ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.buttonText}>
                {pushToken ? 'Subscribe for alerts' : 'Enable notifications to subscribe'}
              </Text>
            )}
          </Pressable>
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f8fafc' },
  scroll: { padding: 20, paddingBottom: 40 },
  spacer: { height: 16 },
  subscribeCard: {
    backgroundColor: '#fff',
    borderRadius: 16,
    padding: 20,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  subscribeTitle: { fontSize: 18, fontWeight: '700', color: '#0f172a' },
  subscribeBody: { fontSize: 14, color: '#475569', marginTop: 6, marginBottom: 14 },
  button: {
    backgroundColor: '#0f172a',
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
  },
  buttonDisabled: { backgroundColor: '#94a3b8' },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '700' },
  confirmed: { backgroundColor: '#dcfce7', borderRadius: 16, padding: 20 },
  confirmedTitle: { fontSize: 18, fontWeight: '800', color: '#166534' },
  confirmedBody: { fontSize: 14, color: '#166534', marginTop: 8, lineHeight: 20 },
});
