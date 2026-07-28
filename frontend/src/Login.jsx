import { useEffect, useRef } from "react";
import { iniciarSesionGoogle } from "./api";

/**
 * Botón de inicio de sesión con Google.
 *
 * Carga la librería de Google bajo demanda y dibuja el botón oficial. El navegador le
 * entrega a Google la contraseña, nunca a esta aplicación: acá solo llega un token
 * firmado que el backend valida.
 *
 * Si no hay VITE_GOOGLE_CLIENT_ID configurado, no se muestra nada: el asistente funciona
 * igual sin cuenta.
 */
export default function Login({ onEntrar }) {
  const contenedor = useRef(null);
  const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID;

  useEffect(() => {
    if (!clientId || !contenedor.current) return;

    const dibujar = () => {
      if (!window.google?.accounts?.id || !contenedor.current) return;
      window.google.accounts.id.initialize({
        client_id: clientId,
        callback: async ({ credential }) => {
          try {
            const datos = await iniciarSesionGoogle(credential);
            onEntrar?.(datos);
          } catch (e) {
            console.error("Falló el inicio de sesión:", e);
          }
        }
      });
      // Google solo permite ajustar estas propiedades del botón; el resto de la
      // apariencia se acomoda desde CSS envolviéndolo (ver .login-google en styles.css).
      window.google.accounts.id.renderButton(contenedor.current, {
        type: "standard",
        theme: "outline",
        size: "large",
        shape: "pill",
        text: "signin_with",
        logo_alignment: "center",
        locale: "es-419",
        width: 260
      });
    };

    if (window.google?.accounts?.id) {
      dibujar();
      return;
    }
    const s = document.createElement("script");
    s.src = "https://accounts.google.com/gsi/client";
    s.async = true;
    s.defer = true;
    s.onload = dibujar;
    document.head.appendChild(s);
  }, [clientId, onEntrar]);

  if (!clientId) return null;
  return <div ref={contenedor} className="login-google" />;
}
