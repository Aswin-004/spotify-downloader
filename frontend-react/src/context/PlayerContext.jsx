import { createContext, useContext, useRef, useState, useCallback, useEffect } from 'react';

const PlayerContext = createContext(null);

export function usePlayer() {
  return useContext(PlayerContext);
}

export function PlayerProvider({ children }) {
  const audioRef  = useRef(null);
  const queueRef  = useRef([]);
  const idxRef    = useRef(-1);

  const [nowPlaying,  setNowPlaying]  = useState(null);
  const [playing,     setPlaying]     = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration,    setDuration]    = useState(0);
  const [volume,      setVolumeState] = useState(1);
  const [queue,       setQueue]       = useState([]);
  const [queueIndex,  setQueueIndex]  = useState(-1);

  // Core: load a track and start playing
  const _load = useCallback((track, index) => {
    const audio = audioRef.current;
    if (!audio || !track?.audioUrl) return;
    audio.src = track.audioUrl;
    audio.load();
    audio.play().catch(() => {});
    setNowPlaying(track);
    setCurrentTime(0);
    setDuration(0);
    setQueueIndex(index);
    idxRef.current = index;
  }, []);

  // Create audio element once, wire all events
  useEffect(() => {
    const audio = new Audio();
    audio.volume = 1;
    audioRef.current = audio;

    const onTime  = () => setCurrentTime(audio.currentTime);
    const onDur   = () => setDuration(isNaN(audio.duration) ? 0 : audio.duration);
    const onPlay  = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onEnded = () => {
      const next = idxRef.current + 1;
      if (next < queueRef.current.length) {
        _load(queueRef.current[next], next);
      } else {
        setPlaying(false);
      }
    };

    audio.addEventListener('timeupdate',     onTime);
    audio.addEventListener('durationchange', onDur);
    audio.addEventListener('play',           onPlay);
    audio.addEventListener('pause',          onPause);
    audio.addEventListener('ended',          onEnded);

    return () => {
      audio.pause();
      audio.src = '';
      audio.removeEventListener('timeupdate',     onTime);
      audio.removeEventListener('durationchange', onDur);
      audio.removeEventListener('play',           onPlay);
      audio.removeEventListener('pause',          onPause);
      audio.removeEventListener('ended',          onEnded);
    };
  }, [_load]);

  const playTrack = useCallback((track) => {
    queueRef.current = [track];
    setQueue([track]);
    _load(track, 0);
  }, [_load]);

  const setQueueAndPlay = useCallback((tracks, index = 0) => {
    if (!tracks?.length) return;
    queueRef.current = tracks;
    setQueue(tracks);
    _load(tracks[index] ?? tracks[0], index);
  }, [_load]);

  const toggle = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || !audio.src) return;
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
  }, []);

  const playNext = useCallback(() => {
    const next = idxRef.current + 1;
    if (next < queueRef.current.length) _load(queueRef.current[next], next);
  }, [_load]);

  const playPrev = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.currentTime > 3) { audio.currentTime = 0; return; }
    const prev = idxRef.current - 1;
    if (prev >= 0) _load(queueRef.current[prev], prev);
  }, [_load]);

  const seek = useCallback((time) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = Math.max(0, Math.min(time, audio.duration || Infinity));
  }, []);

  const changeVolume = useCallback((v) => {
    const audio = audioRef.current;
    if (!audio) return;
    const clamped = Math.max(0, Math.min(1, v));
    audio.volume = clamped;
    setVolumeState(clamped);
  }, []);

  return (
    <PlayerContext.Provider value={{
      nowPlaying, playing, currentTime, duration, volume,
      queue, queueIndex,
      playTrack, setQueueAndPlay, toggle, playNext, playPrev, seek, changeVolume,
    }}>
      {children}
    </PlayerContext.Provider>
  );
}
