import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import {
  Headphones, Clock, Target, Flame, ArrowRight, CheckCircle2,
  ListChecks, AlertTriangle, RefreshCw, Loader2,
  Disc3, ClipboardList, PlayCircle, Sliders, FlagTriangleRight, Ear, Info,
} from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import BPMDisplay from '@/components/BPMDisplay';
import GenreBadge from '@/components/GenreBadge';
import ConfidenceBar from '@/components/ConfidenceBar';
import { api } from '@/services/api';
import { ease } from '@/lib/motion';

const STAGE_LABELS = {
  WARM_UP: 'Warm Up',
  TECHNIQUE: 'Technique',
  ENERGY_CONTROL: 'Energy Control',
  CROSSOVER: 'Crossover',
  CHALLENGE: 'Challenge',
};

const DIFFICULTY_VARIANT = { EASY: 'success', MEDIUM: 'warning', HARD: 'danger' };
const OVERALL_DIFFICULTY_VARIANT = { BEGINNER: 'success', INTERMEDIATE: 'warning', ADVANCED: 'danger' };

function Skeleton({ className = '' }) {
  return <div className={`shimmer rounded-xl ${className}`} />;
}

function TrackSide({ label, track }) {
  return (
    <div className="flex-1 min-w-0">
      <div className="text-11 font-semibold uppercase tracking-wide mb-1" style={{ color: 'var(--text-tertiary)' }}>
        {label}
      </div>
      <div className="text-13 font-medium truncate" style={{ color: 'var(--text-primary)' }} title={track.title}>
        {track.title || 'Untitled'}
      </div>
      <div className="text-12 truncate mb-1.5" style={{ color: 'var(--text-secondary)' }} title={track.artist}>
        {track.artist || 'Unknown'}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <BPMDisplay bpm={track.bpm} musicalKey={track.key} camelot={track.camelot} />
        <GenreBadge genre={track.genre?.split('/').pop()} />
        {track.length_class === 'dj_mix' && (
          <Badge variant="secondary" title="Long-form DJ mix, not a single track">MIX</Badge>
        )}
      </div>
      {track.key_confidence != null && (
        <div className="mt-1.5 max-w-[160px]">
          <ConfidenceBar pct={track.key_confidence * 100} label="Key" compact />
        </div>
      )}
    </div>
  );
}

function MixPlanStep({ icon: Icon, label, children, accent = 'violet' }) {
  return (
    <div>
      <div
        className="flex items-center gap-1.5 text-11 font-semibold uppercase tracking-wide mb-1.5"
        style={{ color: 'var(--text-secondary)' }}
      >
        <Icon className="w-3.5 h-3.5" style={{ color: `var(--accent-${accent})` }} /> {label}
      </div>
      {children}
    </div>
  );
}

function MixPlanList({ items, dot = 'violet' }) {
  return (
    <ul className="space-y-1">
      {items.map((line, i) => (
        <li key={i} className="text-12 flex gap-1.5" style={{ color: 'var(--text-secondary)' }}>
          <span style={{ color: `var(--accent-${dot})` }}>•</span> {line}
        </li>
      ))}
    </ul>
  );
}

function MixPlanText({ text }) {
  return <p className="text-12" style={{ color: 'var(--text-secondary)' }}>{text}</p>;
}

// Renders the Phase 3.1 structured MixPlan in the exact flow requested:
// Deck Setup -> Before You Start -> Start Mix -> First 16 Bars ->
// Next 16 Bars -> EQ/Bass Swap -> Finish -> Listen For -> Success.
function MixPlanSection({ plan }) {
  if (!plan) return null;
  return (
    <div
      className="space-y-3 rounded-lg p-3 border"
      style={{ background: 'var(--surface-1)', borderColor: 'var(--border-subtle)' }}
    >
      <div className="flex items-center gap-1.5 text-11 font-bold uppercase tracking-wide" style={{ color: 'var(--accent-violet)' }}>
        <ClipboardList className="w-3.5 h-3.5" /> Step-by-Step Mix Plan
      </div>

      <MixPlanStep icon={Disc3} label="Deck Setup">
        <div className="text-12 space-y-0.5" style={{ color: 'var(--text-secondary)' }}>
          <div>Deck A → <span style={{ color: 'var(--text-primary)' }}>{plan.deck_a.title}</span> ({plan.source_bpm} BPM • {plan.source_key})</div>
          <div>Deck B → <span style={{ color: 'var(--text-primary)' }}>{plan.deck_b.title}</span> ({plan.target_bpm} BPM • {plan.target_key})</div>
          <div className="pt-1" style={{ color: 'var(--text-tertiary)' }}>{plan.bpm_adjustment}</div>
        </div>
      </MixPlanStep>

      <MixPlanStep icon={ClipboardList} label="Before You Start">
        <MixPlanText text={plan.cue_instruction} />
      </MixPlanStep>

      <MixPlanStep icon={PlayCircle} label="Start Mix">
        <MixPlanText text={plan.start_instruction} />
      </MixPlanStep>

      <MixPlanStep icon={ListChecks} label="First 16 Bars">
        <MixPlanList items={plan.phase_1} />
      </MixPlanStep>

      <MixPlanStep icon={ListChecks} label="Next 16 Bars">
        <MixPlanList items={plan.phase_2} />
      </MixPlanStep>

      <MixPlanStep icon={Sliders} label="EQ / Bass Swap">
        <MixPlanText text={plan.eq_instruction} />
      </MixPlanStep>

      <MixPlanStep icon={FlagTriangleRight} label="Finish">
        <MixPlanText text={plan.transition_finish} />
      </MixPlanStep>

      <MixPlanStep icon={Ear} label="Listen For" accent="amber">
        <MixPlanList items={plan.listen_for} dot="amber" />
      </MixPlanStep>

      <MixPlanStep icon={CheckCircle2} label="Success" accent="emerald">
        <MixPlanList items={plan.success_criteria} dot="emerald" />
      </MixPlanStep>

      {plan.limitations?.length > 0 && (
        <div className="flex items-start gap-1.5 pt-1 border-t" style={{ borderColor: 'var(--border-subtle)' }}>
          <Info className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" style={{ color: 'var(--text-tertiary)' }} />
          <div className="text-11" style={{ color: 'var(--text-tertiary)' }}>
            {plan.limitations.join(' ')}
          </div>
        </div>
      )}
    </div>
  );
}

function ExerciseCard({ exercise, index }) {
  const m = exercise.metrics || {};
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...ease, delay: index * 0.05 }}
    >
      <Card>
        <CardHeader className="flex-row items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2">
            <span
              className="w-6 h-6 rounded-full flex items-center justify-center text-11 font-bold flex-shrink-0"
              style={{ background: 'var(--accent-violet-dim)', color: 'var(--accent-violet)' }}
            >
              {index + 1}
            </span>
            <div>
              <div className="flex items-center gap-1.5">
                <Badge variant="info">{STAGE_LABELS[exercise.stage] || exercise.stage}</Badge>
                <Badge variant={DIFFICULTY_VARIANT[exercise.difficulty] || 'default'}>{exercise.difficulty}</Badge>
              </div>
              <CardTitle className="mt-1">{exercise.title}</CardTitle>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-3 rounded-lg p-3" style={{ background: 'var(--surface-1)' }}>
            <TrackSide label="Source" track={exercise.source_track} />
            <ArrowRight className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--text-tertiary)' }} />
            <TrackSide label="Target" track={exercise.target_track} />
          </div>

          <div className="flex flex-wrap gap-3 text-11 font-mono" style={{ color: 'var(--text-tertiary)' }}>
            {m.bpm_delta != null && <span>Δ BPM {m.bpm_delta}</span>}
            {m.energy_delta != null && <span>Δ Energy {m.energy_delta}{m.energy_direction ? ` (${m.energy_direction})` : ''}</span>}
            {m.harmonic_relation && <span>{m.harmonic_relation.replace('_', ' ')}</span>}
            {m.difficulty_score != null && <span>difficulty {m.difficulty_score}</span>}
          </div>

          {/* PHASE 3.1 FINAL UX FIX 2: the old Phase 3 "Instructions"/
              "Success Criteria" sections are intentionally not rendered
              here anymore — MixPlanSection above is the one primary,
              actionable workflow. exercise.instructions/success_criteria
              stay in the API response for backward compatibility (and
              exercise.success_criteria is now literally the same list as
              mix_plan.success_criteria, not an independently-duplicated
              text — see git show 5b579ea:docs/PHASE_3_1_ACTIONABLE_MIX_PLAN.md §14), they're
              just not shown twice in the UI. */}
        </CardContent>
      </Card>
    </motion.div>
  );
}

export default function DjCoach() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  function load() {
    setLoading(true);
    setError(null);
    api.getDjCoachToday()
      .then(setSession)
      .catch((e) => setError(e.message || 'Failed to load today’s session'))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto space-y-4">
        <Skeleton className="h-24 w-full" />
        {[1, 2, 3, 4, 5].map(i => <Skeleton key={i} className="h-48 w-full" />)}
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-4xl mx-auto">
        <Card>
          <CardContent className="flex items-center gap-3 py-6">
            <AlertTriangle className="w-5 h-5 flex-shrink-0" style={{ color: 'var(--accent-rose)' }} />
            <div className="flex-1">
              <div className="text-13 font-medium" style={{ color: 'var(--text-primary)' }}>Couldn't load today's session</div>
              <div className="text-12" style={{ color: 'var(--text-secondary)' }}>{error}</div>
            </div>
            <Button onClick={load} variant="secondary"><RefreshCw className="w-3.5 h-3.5 mr-1.5" /> Retry</Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!session || !session.exercises?.length) {
    return (
      <div className="max-w-4xl mx-auto">
        <Card>
          <CardContent className="py-8 text-center">
            <Headphones className="w-8 h-8 mx-auto mb-2" style={{ color: 'var(--text-tertiary)' }} />
            <div className="text-13 font-medium mb-1" style={{ color: 'var(--text-primary)' }}>No session available today</div>
            <div className="text-12" style={{ color: 'var(--text-secondary)' }}>
              {session?.error || 'Not enough analyzed tracks in your library yet to build a full session.'}
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  const { title, objective, difficulty, estimated_duration_minutes, exercises, coach_summary, error: sessionError } = session;

  return (
    <div className="max-w-4xl mx-auto space-y-4">
      <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} transition={ease}>
        <Card>
          <CardContent className="py-5 space-y-3">
            <div className="flex items-center gap-2">
              <Headphones className="w-5 h-5" style={{ color: 'var(--accent-violet)' }} />
              <h1 className="font-display text-18 font-bold" style={{ color: 'var(--text-primary)' }}>{title}</h1>
            </div>

            {objective && (
              <p className="text-13" style={{ color: 'var(--text-secondary)' }}>{objective.description}</p>
            )}

            <div className="flex flex-wrap items-center gap-4 pt-1">
              {difficulty && (
                <div className="flex items-center gap-1.5">
                  <Flame className="w-3.5 h-3.5" style={{ color: 'var(--text-tertiary)' }} />
                  <Badge variant={OVERALL_DIFFICULTY_VARIANT[difficulty.label] || 'default'}>{difficulty.label}</Badge>
                  <span className="text-11 font-mono" style={{ color: 'var(--text-tertiary)' }}>
                    score {difficulty.score} · {difficulty.progression.replace(/_/g, ' ').toLowerCase()}
                  </span>
                </div>
              )}
              <div className="flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5" style={{ color: 'var(--text-tertiary)' }} />
                <span className="text-12" style={{ color: 'var(--text-secondary)' }}>~{estimated_duration_minutes} min</span>
              </div>
              {objective && (
                <div className="flex items-center gap-1.5">
                  <Target className="w-3.5 h-3.5" style={{ color: 'var(--text-tertiary)' }} />
                  <span className="text-12" style={{ color: 'var(--text-secondary)' }}>Focus: {objective.focus}</span>
                </div>
              )}
            </div>

            {objective?.skills?.length > 0 && (
              <div className="flex flex-wrap gap-1.5 pt-1">
                {objective.skills.map((s, i) => (
                  <Badge key={i} variant="secondary">{s}</Badge>
                ))}
              </div>
            )}

            {coach_summary && (
              <p className="text-11 italic pt-1" style={{ color: 'var(--text-tertiary)' }}>{coach_summary}</p>
            )}

            {sessionError && (
              <p className="text-11 flex items-center gap-1.5" style={{ color: 'var(--accent-amber)' }}>
                <AlertTriangle className="w-3.5 h-3.5" /> {sessionError}
              </p>
            )}
          </CardContent>
        </Card>
      </motion.div>

      {exercises.map((ex, i) => (
        <ExerciseCard key={ex.exercise_id || i} exercise={ex} index={i} />
      ))}
    </div>
  );
}
