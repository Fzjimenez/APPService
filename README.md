# Archivo · Laboratorio Azure

Aplicación Flask lista para subir a GitHub y desplegar en **Azure App Service Linux**. Permite subir, listar, buscar y descargar archivos. Interfaz en español y horas de Costa Rica. No requiere base de datos ni Docker.

## 1. Probar ahora en Windows

Necesitás Python 3.12 instalado. Abrí PowerShell en esta carpeta:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe app.py
```

Abrí http://127.0.0.1:5000. El modo `demo` guarda archivos reales en `.data/`, únicamente en tu equipo. La página indica claramente que no usa Azure. No hay archivos de ejemplo precargados. Para detener: Ctrl+C. En macOS/Linux, usá `python3`, `cp` y `.venv/bin/python`.

## 2. Subir a tu repositorio

Copiá **el contenido de esta carpeta a la raíz del repositorio**, de modo que `app.py` y `requirements.txt` queden en la raíz. `.gitignore` excluye `.env`, archivos cargados y entorno virtual.

```bash
git add .
git commit -m "Crear portal de archivos del laboratorio"
git push
```

Si el repositorio aún no está inicializado, primero inicializalo y asociá tu remoto con tus comandos Git habituales. No se ha publicado nada desde este paquete.

## 3. Crear el App Service

- Publicación: **Code**; sistema operativo: **Linux**; runtime **Python 3.12**.
- Plan con soporte para VNet Integration (por ejemplo Basic B1 para el laboratorio; revisar disponibilidad y precio en tu región).
- HTTPS Only habilitado. Acceso público para la web, protegido por inicio de sesión.
- Comando de inicio en Configuration / General settings o Stack settings:

```text
python -m gunicorn --bind=0.0.0.0:8000 --workers=2 --threads=4 --timeout=120 app:app
```

En Environment variables / App settings configurar:

| Variable | Valor |
|---|---|
| `STORAGE_MODE` | `azure` |
| `AZURE_STORAGE_ACCOUNT` | Nombre real de tu Storage Account, sin URL |
| `AZURE_STORAGE_CONTAINER` | `documentos` |
| `FLASK_SECRET_KEY` | Clave aleatoria exclusiva del sitio |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` |

Generar la clave localmente con `python -c "import secrets; print(secrets.token_hex(32))"` y copiarla al ajuste de App Service. Es para proteger la sesión web, **no es una clave de Storage**. No subirla al repo. Todas las instancias deben usar la misma. Reiniciar después de guardar ajustes.

## 4. Inicio de sesión de participantes

En **App Service → Authentication → Add identity provider → Microsoft**:

1. Configurar Microsoft Entra para el tenant del laboratorio (single tenant).
2. Exigir autenticación y redirigir solicitudes no autenticadas al inicio de sesión.
3. En la aplicación empresarial de Entra, habilitar **Assignment required** y asignar los usuarios o grupos participantes.
4. Probar en una ventana privada: un usuario asignado entra; uno no asignado no debe entrar.

App Service autentica a las personas. La aplicación comprueba el encabezado de identidad que inyecta App Service. **No implementar este esquema en un servidor público diferente que permita falsificar ese encabezado.** El servidor local solo acepta loopback. No usarlo detrás de túneles o proxies públicos.

Todos los participantes autorizados comparten los mismos documentos. No hay carpetas ni permisos por usuario. La identidad administrada de la aplicación es independiente del inicio de sesión de los participantes.

## 5. Conectar con Blob Storage

1. Crear Storage Account y un contenedor `documentos` de acceso anónimo privado. La aplicación no crea el contenedor automáticamente.
2. En **App Service → Identity → System assigned → On**, habilitar la identidad.
3. En el contenedor, asignar a esa identidad **Storage Blob Data Contributor** con alcance al contenedor. Este rol también permite eliminar blobs, aunque la aplicación no expone esa función.
4. Esperar la propagación de permisos y usar **Actualizar** en la web.
5. Subir un archivo ficticio. Se crea un blob con prefijo `archivos/` y un identificador aleatorio para evitar sobrescrituras. Los nombres se normalizan a caracteres seguros.

En Azure, el código usa `ManagedIdentityCredential` (identidad del sistema). Nunca usa la cuenta del desarrollador, claves de Storage ni cadenas de conexión. Para desarrollo local contra Azure: `STORAGE_MODE=azure`, configurar cuenta/contenedor, ejecutar `az login` y asignar a tu usuario el rol de datos correspondiente. Localmente usa `AzureCliCredential`.

## 6. Desplegar desde GitHub con OIDC

Elegí **una** de estas alternativas:

### A. Deployment Center del portal

Seleccionar GitHub, repositorio y rama, y configurar GitHub Actions con autenticación OpenID Connect. El portal genera su workflow. Eliminá el `deploy.yml` incluido para mantener un único flujo de despliegue. Revisá que el workflow generado publique código fuente y que la compilación remota instale `requirements.txt`.

### B. Workflow incluido en este paquete

El archivo `.github/workflows/deploy.yml` es de ejecución **manual** para evitar publicar antes de configurar Azure.

1. Crear una identidad administrada **asignada por el usuario** para despliegues (distinta de la identidad del App Service que accede a Storage).
2. Asignarle `Website Contributor` con alcance al App Service.
3. Agregar credencial federada GitHub: organización/usuario, repositorio y rama `main`. Subject: `repo:TU_USUARIO/TU_REPO:ref:refs/heads/main`; issuer: `https://token.actions.githubusercontent.com`; audience: `api://AzureADTokenExchange`.
4. En GitHub → Settings → Secrets and variables → Actions → **Variables**, crear:
   - `AZURE_WEBAPP_NAME`: nombre del App Service.
   - `AZURE_CLIENT_ID`: client ID de la identidad de despliegue.
   - `AZURE_TENANT_ID`: tenant ID.
   - `AZURE_SUBSCRIPTION_ID`: subscription ID.
5. Habilitar compilación remota con `SCM_DO_BUILD_DURING_DEPLOYMENT=true` y configurar el comando de inicio indicado arriba.
6. GitHub → Actions → Deploy portal to Azure → Run workflow, sobre `main`.

No requiere publish profile ni contraseña de Azure en GitHub. El endpoint de despliegue de App Service debe ser accesible desde el runner. Si después querés despliegue automático, agregá `push: {branches: [main]}` bajo `on`, conservando `workflow_dispatch`.

## 7. Convertir la conexión a privada

- VNet `10.20.0.0/16`, misma región que App Service.
- Subred `snet-app-integration`, `10.20.1.0/24`, delegada a `Microsoft.Web/serverFarms`.
- Subred `snet-private-endpoints`, `10.20.2.0/24`, sin esa delegación.
- Habilitar VNet Integration en App Service con la primera subred.
- Crear Private Endpoint de Storage para **blob** en la segunda.
- Integrar la zona `privatelink.blob.core.windows.net` y vincularla a la VNet.
- Comprobar DNS privado desde App Service; después deshabilitar public network access en Storage y repetir carga/listado/descarga.

El navegador habla con App Service. El backend habla con Storage. No se necesitan CORS ni SAS para que el navegador acceda al blob. El nombre del Storage en configuración no cambia al activar Private Endpoint. Para acceder localmente a Storage una vez cerrado, el equipo debe tener ruta y DNS a la red privada; el modo demo seguirá funcionando sin Azure.

## 8. Límites y pruebas

- Máximo 10 MB por archivo, formatos PDF/TXT/CSV/PNG/JPG/DOCX/XLSX/PPTX.
- Lista hasta 1.000 archivos del prefijo `archivos/`; búsqueda sobre los cargados en esa lista.
- Archivos siempre descargados como adjuntos. Sin vista previa ni ejecución.
- Comprobación de extensión, no análisis antivirus: usar documentos ficticios. Para archivos reales, evaluar análisis de malware y los controles de la organización.
- Protección CSRF, rechazo de rutas inválidas, nombres seguros y límite de tamaño en servidor.
- Los errores no revelan secretos ni trazas al navegador; los detalles quedan en registros del servidor.

Ejecutar pruebas:

```bash
python -m pip install pytest
python -m pytest -q
```

Las pruebas validan el flujo local, controles de acceso, tamaño y contrato básico del adaptador Blob con dobles de prueba. **No sustituyen la prueba real en Azure.** No se ha conectado esta entrega a una suscripción o repositorio del cliente.

Prueba de aceptación: entrar con participante autorizado; subir documento; actualizar; buscar; descargar y comparar; quitar permiso de datos y comprobar rechazo (considerar caché/propagación); restaurarlo; activar conexión privada y repetir.

## Referencias

- https://learn.microsoft.com/en-us/azure/app-service/deploy-github-actions
- https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-user-identities
- https://learn.microsoft.com/en-us/azure/app-service/containers/how-to-configure-python
- https://learn.microsoft.com/en-us/azure/app-service/overview-vnet-integration
- https://learn.microsoft.com/en-us/azure/storage/blobs/storage-quickstart-blobs-python
