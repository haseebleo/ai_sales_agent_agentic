import { useEffect, useRef, useState } from 'react';

interface AudioVisualizerProps {
  isActive: boolean;
  state: string;
}

const BAR_COUNT = 7;

export const AudioVisualizer: React.FC<AudioVisualizerProps> = ({
  isActive,
  state,
}) => {
  const [bars, setBars] = useState<number[]>(Array(BAR_COUNT).fill(4));
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!isActive) {
      setBars(Array(BAR_COUNT).fill(4));
      if (timerRef.current) clearTimeout(timerRef.current);
      return;
    }

    const maxHeight = state === 'speaking' ? 36 : state === 'processing' ? 18 : 24;
    const speed = state === 'processing' ? 200 : 120;

    const animate = () => {
      setBars(
        Array.from({ length: BAR_COUNT }, () =>
          4 + Math.random() * maxHeight
        )
      );
      timerRef.current = setTimeout(animate, speed);
    };
    animate();

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [isActive, state]);

  const color =
    state === 'speaking'
      ? 'var(--accent-main)'
      : state === 'processing'
        ? 'var(--accent-purple)'
        : 'var(--accent-green)';

  return (
    <div
      style={{
        display: 'flex',
        gap: '3px',
        alignItems: 'center',
        justifyContent: 'center',
        height: '44px',
        padding: '4px 0',
      }}
    >
      {bars.map((h, i) => (
        <div
          key={i}
          style={{
            width: '5px',
            height: `${h}px`,
            backgroundColor: color,
            borderRadius: '3px',
            transition: `height ${state === 'processing' ? '200ms' : '100ms'} ease`,
            opacity: 0.8,
          }}
        />
      ))}
    </div>
  );
};
