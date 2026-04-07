export default function ClusterPreview({ clusters }) {
  return (
    <section className="card">
      <h2>Image Grouping Preview</h2>
      <div className="cluster-grid">
        {clusters.map((cluster) => (
          <div className="cluster" key={cluster.id}>
            <strong>Cluster #{cluster.id}</strong>
            <p>{cluster.image_count} images</p>
          </div>
        ))}
      </div>
    </section>
  );
}
