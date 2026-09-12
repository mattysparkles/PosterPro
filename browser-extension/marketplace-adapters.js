globalThis.POSTERPRO_MARKETPLACE_ADAPTERS = Object.freeze({
  facebook: {
    hosts: ["facebook.com"],
    title: ['input[aria-label*="Title" i]', 'input[placeholder*="Title" i]'],
    price: ['input[aria-label*="Price" i]', 'input[placeholder*="Price" i]', 'input[inputmode="numeric"]'],
    description: ['textarea[aria-label*="Description" i]', 'textarea[placeholder*="Description" i]'],
    photos: 'input[type="file"]',
  },
  mercari: {
    hosts: ["mercari.com"],
    title: ['input[name*="title" i]', 'input[placeholder*="Title" i]', 'input[maxlength="80"]'],
    price: ['input[name*="price" i]', 'input[placeholder*="Price" i]', 'input[inputmode="numeric"]'],
    description: ['textarea[name*="description" i]', 'textarea[placeholder*="Describe" i]'],
    photos: 'input[type="file"]',
  },
  poshmark: {
    hosts: ["poshmark.com"],
    title: ['input[name*="title" i]', 'input[placeholder*="Title" i]', 'input[maxlength="50"]'],
    price: ['input[name*="price" i]', 'input[placeholder*="List price" i]', 'input[inputmode="numeric"]'],
    description: ['textarea[name*="description" i]', 'textarea[placeholder*="Describe" i]'],
    photos: 'input[type="file"]',
  },
  vinted: {
    hosts: ["vinted.com"],
    title: ['input[name*="title" i]', 'input[placeholder*="Title" i]'],
    price: ['input[name*="price" i]', 'input[placeholder*="Price" i]', 'input[inputmode="decimal"]'],
    description: ['textarea[name*="description" i]', 'textarea[placeholder*="Description" i]'],
    photos: 'input[type="file"]',
  },
  etsy: {
    hosts: ["etsy.com"],
    title: ['input[name*="title" i]', 'input[placeholder*="Title" i]', 'input[maxlength="140"]'],
    price: ['input[name*="price" i]', 'input[placeholder*="Price" i]', 'input[inputmode="decimal"]'],
    description: ['textarea[name*="description" i]', 'textarea[placeholder*="Describe" i]'],
    photos: 'input[type="file"]',
  },
  offerup: {
    hosts: ["offerup.com"],
    title: ['input[name*="title" i]', 'input[placeholder*="Title" i]'],
    price: ['input[name*="price" i]', 'input[placeholder*="Price" i]', 'input[inputmode="decimal"]'],
    description: ['textarea[name*="description" i]', 'textarea[placeholder*="Description" i]'],
    photos: 'input[type="file"]',
  },
});
