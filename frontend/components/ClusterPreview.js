import { Images } from 'lucide-react';

import { Card, CardDescription, CardTitle } from './ui/card';

export default function ClusterPreview({ clusters }) {
  return (
    <Card data-tour="upload-photos">
      <CardTitle className="flex items-center gap-2"><Images size={18} /> Upload Photos</CardTitle>
      <CardDescription className="mb-4">Your newest photo groups are listed here so you can quickly verify inventory chunks.</CardDescription>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {clusters.map((cluster) => (
          <div key={cluster.id} className="rounded-2xl border border-border/70 bg-background p-4">
            <p className="font-semibold">Cluster #{cluster.id}</p>
            <p className="text-sm text-muted-foreground">{cluster.image_count} photos ready</p>
          </div>
        ))}
      </div>
    </Card>
  );
}
