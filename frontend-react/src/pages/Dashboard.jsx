import DownloadInput from '@/components/DownloadInput';
import StatsCards from '@/components/StatsCards';
import QueueCard from '@/components/QueueCard';
import ActivityFeed from '@/components/ActivityFeed';

export default function Dashboard() {
  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <DownloadInput />
      <StatsCards />
      <QueueCard />

      {/* Mobile activity feed */}
      <div className="xl:hidden">
        <div className="rounded-2xl border border-border bg-surface overflow-hidden h-[400px]">
          <ActivityFeed />
        </div>
      </div>
    </div>
  );
}
