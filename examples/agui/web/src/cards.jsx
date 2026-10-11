// Tool name -> React component. Each component gets the tool's data (its artifact) and onAsk(text), which
// sends a new message, so buttons on a card start a new turn. Same file in custom_ui/web and agui/web.
import { useState } from "react";

export const TOOL_LABELS = {
  search_products: "Searching products",
  get_products_in_category: "Loading the category",
  list_categories: "Loading categories",
  get_product: "Loading product details",
  compare_products: "Comparing products",
  get_my_cart: "Loading your cart",
};

const money = (value) => `$${Number(value).toFixed(2)}`;
// Images come from the API; only allow https URLs
const imageUrl = (url) => (typeof url === "string" && url.startsWith("https://") ? url : undefined);

function Rating({ rating }) {
  if (!rating) return null;
  return (
    <span className="rating" title={`${rating.rate} out of 5`}>
      ★ {rating.rate} <span className="muted">({rating.count})</span>
    </span>
  );
}

function ProductGrid({ data, onAsk }) {
  const [selected, setSelected] = useState([]);
  const toggle = (id) => setSelected((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));
  return (
    <div className="block">
      <div className="grid">
        {data.map((product) => (
          <article key={product.id} className={`product${selected.includes(product.id) ? " selected" : ""}`}>
            <img src={imageUrl(product.image)} alt="" loading="lazy" />
            <div className="category">{product.category}</div>
            <h3 title={product.title}>{product.title}</h3>
            <div className="row">
              <strong>{money(product.price)}</strong>
              <Rating rating={product.rating} />
            </div>
            <div className="row">
              <button onClick={() => onAsk(`Show me the details of product #${product.id}`)}>Details</button>
              <label className="muted">
                <input type="checkbox" checked={selected.includes(product.id)} onChange={() => toggle(product.id)} />
                Compare
              </label>
            </div>
          </article>
        ))}
      </div>
      {selected.length >= 2 && (
        <button className="primary" onClick={() => onAsk(`Compare products ${selected.map((id) => `#${id}`).join(", ")}`)}>
          Compare {selected.length} products
        </button>
      )}
    </div>
  );
}

function Categories({ data, onAsk }) {
  return (
    <div className="chips">
      {data.map((category) => (
        <button key={category} className="chip" onClick={() => onAsk(`Show me ${category}`)}>
          {category}
        </button>
      ))}
    </div>
  );
}

function ProductDetail({ data: product, onAsk }) {
  return (
    <article className="detail">
      <img src={imageUrl(product.image)} alt="" />
      <div>
        <div className="category">{product.category}</div>
        <h2>{product.title}</h2>
        <div className="row">
          <strong className="price">{money(product.price)}</strong>
          <Rating rating={product.rating} />
        </div>
        <p>{product.description}</p>
        <button onClick={() => onAsk(`Find products similar to #${product.id} "${product.title}"`)}>Similar products</button>
      </div>
    </article>
  );
}

function CompareTable({ data }) {
  const lowest = Math.min(...data.map((p) => p.price));
  const best = Math.max(...data.map((p) => p.rating?.rate ?? 0));
  return (
    <div className="table-wrap">
      <table className="compare">
        <thead>
          <tr>
            <th />
            {data.map((p) => (
              <th key={p.id}>
                <img src={imageUrl(p.image)} alt="" />
                <div>{p.title}</div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            <th>Price</th>
            {data.map((p) => <td key={p.id} className={p.price === lowest ? "best" : ""}>{money(p.price)}</td>)}
          </tr>
          <tr>
            <th>Rating</th>
            {data.map((p) => <td key={p.id} className={p.rating?.rate === best ? "best" : ""}><Rating rating={p.rating} /></td>)}
          </tr>
          <tr>
            <th>Category</th>
            {data.map((p) => <td key={p.id}>{p.category}</td>)}
          </tr>
          <tr>
            <th>Description</th>
            {data.map((p) => <td key={p.id} className="description">{p.description}</td>)}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function Cart({ data }) {
  return (
    <div className="cart">
      <div className="row">
        <strong>🛒 Cart #{data.cart_id}</strong>
        <span className="muted">{data.date}</span>
      </div>
      {data.items.map((item) => (
        <div key={item.product.id} className="cart-row">
          <img src={imageUrl(item.product.image)} alt="" />
          <span className="grow">{item.product.title}</span>
          <span className="muted">{item.quantity} × {money(item.product.price)}</span>
          <strong>{money(item.subtotal)}</strong>
        </div>
      ))}
      <div className="row total">
        <span>Total</span>
        <strong>{money(data.total)}</strong>
      </div>
    </div>
  );
}

const RENDERERS = {
  search_products: ProductGrid,
  get_products_in_category: ProductGrid,
  list_categories: Categories,
  get_product: ProductDetail,
  compare_products: CompareTable,
  get_my_cart: Cart,
};

// Renders a tool's data, or nothing for tools without a component and for empty results
export function ToolCard({ name, data, onAsk }) {
  const Component = RENDERERS[name];
  if (!Component || data == null || (Array.isArray(data) && data.length === 0)) return null;
  return <Component data={data} onAsk={onAsk} />;
}

// The reply text, with **bold** and line breaks (React escapes everything else)
export function ReplyText({ text }) {
  return (
    <div className="bubble bot">
      {text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
        part.startsWith("**") && part.endsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : part,
      )}
    </div>
  );
}

export const SUGGESTIONS = [
  "What categories do you have?",
  "Find a backpack under $120",
  "Show me jackets sorted by price",
  "Compare the two SSDs",
  "What's in my cart?",
];
