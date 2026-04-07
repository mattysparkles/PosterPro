import { useMemo, useState } from 'react';
import { Crop, ImagePlus, Sparkles } from 'lucide-react';
import toast from 'react-hot-toast';

import Button from './ui/button';
import { Card, CardDescription, CardTitle } from './ui/card';
import Input from './ui/input';
import { toPublicImageUrl } from '../lib/api';

const FILTERS = ['none', 'vivid', 'mono', 'soft', 'dramatic'];

function initialCrop() {
  return { crop_x: 0, crop_y: 0, crop_width: '', crop_height: '' };
}

export default function PhotoEditorModal({ open, listing, onClose, onApply }) {
  const [brightness, setBrightness] = useState(1);
  const [contrast, setContrast] = useState(1);
  const [filterName, setFilterName] = useState('none');
  const [crop, setCrop] = useState(initialCrop());
  const [sourceImage, setSourceImage] = useState((listing?.image_urls || [])[0] || '');
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState('');
  const [busy, setBusy] = useState(false);

  const previewUrl = useMemo(() => (preview ? preview : toPublicImageUrl(sourceImage || '')), [preview, sourceImage]);

  if (!open || !listing) return null;

  const onFileChange = (next) => {
    if (!next) return;
    setFile(next);
    const objectUrl = URL.createObjectURL(next);
    setPreview(objectUrl);
    setSourceImage('');
  };

  const applyEdits = async (removeBackground = false) => {
    try {
      setBusy(true);
      await onApply({
        listingId: listing.id,
        sourceImage,
        file,
        removeBackground,
        edits: {
          brightness,
          contrast,
          filter_name: filterName,
          crop_x: Number(crop.crop_x) || 0,
          crop_y: Number(crop.crop_y) || 0,
          crop_width: Number(crop.crop_width) || null,
          crop_height: Number(crop.crop_height) || null,
        },
      });
      toast.success(removeBackground ? 'Background removed and saved.' : 'Edited photo saved.');
      onClose();
    } catch (error) {
      toast.error(error.message || 'Photo edit failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-3">
      <Card className="grid max-h-[95vh] w-full max-w-6xl gap-4 overflow-auto p-3 lg:grid-cols-[1.5fr_1fr]">
        <div className="rounded-2xl border border-border/70 bg-muted/20 p-3">
          <CardTitle className="mb-2 text-2xl">Pro Photo Studio</CardTitle>
          <CardDescription className="mb-3">Live preview with premium controls for marketplace-ready photos.</CardDescription>
          <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-background">
            {previewUrl ? (
              <img
                src={previewUrl}
                alt="preview"
                className="max-h-[65vh] rounded-xl object-contain"
                style={{ filter: `brightness(${brightness}) contrast(${contrast})` }}
              />
            ) : (
              <p className="text-sm text-muted-foreground">Drop a photo or select one from this listing.</p>
            )}
          </div>
        </div>

        <div className="space-y-3">
          <div className="rounded-2xl border border-border/70 p-3">
            <p className="mb-2 text-sm font-semibold">Source photo</p>
            <select
              className="mb-2 w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
              value={sourceImage}
              onChange={(e) => { setSourceImage(e.target.value); setPreview(''); setFile(null); }}
              title="Pick an existing listing image for quick edits."
            >
              <option value="">Select from listing photos</option>
              {(listing.image_urls || []).map((img) => <option key={img} value={img}>{img.split('/').slice(-1)[0]}</option>)}
            </select>
            <label className="flex cursor-pointer items-center gap-2 rounded-xl border border-dashed border-border p-3 text-sm" title="Drag/drop or select a new file from your computer.">
              <ImagePlus size={16} /> Upload / drag-drop
              <input className="hidden" type="file" accept="image/*" onChange={(e) => onFileChange(e.target.files?.[0])} />
            </label>
          </div>

          <div className="rounded-2xl border border-border/70 p-3">
            <p className="text-sm font-semibold" title="Fine tune exposure for brighter product photos.">Brightness</p>
            <input type="range" min="0.6" max="1.8" step="0.05" value={brightness} onChange={(e) => setBrightness(Number(e.target.value))} className="w-full" />
            <p className="text-sm font-semibold" title="Boost contrast to make details pop.">Contrast</p>
            <input type="range" min="0.6" max="1.8" step="0.05" value={contrast} onChange={(e) => setContrast(Number(e.target.value))} className="w-full" />
          </div>

          <div className="rounded-2xl border border-border/70 p-3">
            <p className="mb-2 text-sm font-semibold">Filters</p>
            <div className="flex flex-wrap gap-2">
              {FILTERS.map((name) => (
                <Button key={name} size="sm" variant={filterName === name ? 'default' : 'outline'} onClick={() => setFilterName(name)} title={`Apply ${name} filter.`}>
                  {name}
                </Button>
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-border/70 p-3">
            <p className="mb-2 text-sm font-semibold"><Crop size={14} className="mr-1 inline" />Crop tool</p>
            <div className="grid grid-cols-2 gap-2">
              {['crop_x', 'crop_y', 'crop_width', 'crop_height'].map((field) => (
                <Input key={field} placeholder={field} value={crop[field]} onChange={(e) => setCrop((prev) => ({ ...prev, [field]: e.target.value }))} title="Enter crop area in pixels." />
              ))}
            </div>
          </div>

          <div className="grid gap-2">
            <Button size="lg" disabled={busy} onClick={() => applyEdits(true)} title="One-click AI background removal with PhotoRoom.">
              <Sparkles size={18} /> {busy ? 'Working...' : 'Remove Background'}
            </Button>
            <Button size="lg" variant="secondary" disabled={busy} onClick={() => applyEdits(false)} title="Save adjusted version as a new listing image.">
              Save edited photo
            </Button>
            <Button variant="outline" onClick={onClose}>Close</Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
