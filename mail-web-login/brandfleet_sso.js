// A fixed return link; no grant, credential, account action or external script.
document.addEventListener('DOMContentLoaded', () => {
  const menu = document.querySelector('#taskmenu');
  if (!menu || menu.querySelector('.brandfleet-return')) return;
  const link = document.createElement('a');
  link.className = 'button brandfleet-return';
  link.href = 'https://fleet.example.com/';
  link.title = 'Return to BrandFleet';
  const label = document.createElement('span'); label.textContent = 'BrandFleet';
  link.append(label); menu.append(link);
});
