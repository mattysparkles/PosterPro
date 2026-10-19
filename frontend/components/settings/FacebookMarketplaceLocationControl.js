import Button from '../ui/button';
import Input from '../ui/input';

export default function FacebookMarketplaceLocationControl({ value, saving = false, onChange, onSave }) {
  return (
    <div className="mt-4 rounded-xl border border-[#b2ccff] bg-[#f7faff] p-4" data-testid="facebook-location-setting">
      <label htmlFor="facebook-default-marketplace-location" className="text-sm font-semibold text-[#101828]">
        Default Marketplace location
      </label>
      <p className="mt-1 text-sm text-[#475467]">Location Facebook Marketplace should use for your listings.</p>
      <div className="mt-3 flex flex-col gap-2 sm:flex-row">
        <Input
          id="facebook-default-marketplace-location"
          aria-label="Default Marketplace location"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="Enter the location Facebook should use for listings"
          autoComplete="address-level2"
        />
        <Button type="button" onClick={onSave} disabled={saving || !String(value || '').trim()}>
          {saving ? 'Saving…' : 'Save location'}
        </Button>
      </div>
      <p className="mt-2 text-xs text-[#667085]">
        {String(value || '').trim()
          ? 'Saved location is loaded from Marketplace Setup.'
          : 'No location saved yet. PosterPro will not guess a ZIP code or city.'}
      </p>
    </div>
  );
}
