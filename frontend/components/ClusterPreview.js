import Link from 'next/link';
import { ArrowRight, Images } from 'lucide-react';

import Button from './ui/button';
import { Card, CardDescription, CardTitle } from './ui/card';

export default function ClusterPreview({ clusters }) {
  return (
    <Card data-tour="upload-photos">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="max-w-2xl">
          <CardTitle className="flex items-center gap-2 text-xl tracking-tight">
            <Images size={18} />
            Intake and photo batches
          </CardTitle>
          <CardDescription className="mt-2 leading-6 text-slate-600">
            The dashboard keeps incoming photo groups visible so the user can verify inventory chunks before they disappear into deeper workflow pages.
          </CardDescription>
        </div>
        <Link href="/inventory">
          <Button variant="outline">
            Open inventory
            <ArrowRight size={15} />
          </Button>
        </Link>
      </div>

      {!clusters.length ? (
        <div className="mt-6 rounded-[24px] border border-dashed border-slate-300 bg-slate-50 p-5">
          <p className="text-sm font-semibold text-slate-900">No intake batches yet</p>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Once photos or grouped inventory arrive, this area becomes the clearest entry point into the listing workflow.
          </p>
        </div>
      ) : (
        <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {clusters.slice(0, 4).map((cluster) => (
            <div key={cluster.id} className="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
              <p className="text-xs font-bold uppercase tracking-[0.22em] text-slate-400">Batch {cluster.id}</p>
              <p className="mt-3 text-2xl font-semibold tracking-tight text-slate-950">{cluster.image_count}</p>
              <p className="mt-1 text-sm text-slate-600">photos ready for review</p>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
