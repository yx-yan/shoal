#!/usr/bin/env python3
"""Package this folder as shoal-terminals.vsix, with no build tools."""

import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="shoal-terminals" Version="0.0.1" Publisher="local"/>
    <DisplayName>Shoal Terminals</DisplayName>
    <Description xml:space="preserve">Opens a terminal for each running /shoal worker.</Description>
    <Properties><Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.80.0"/></Properties>
  </Metadata>
  <Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation>
  <Dependencies/>
  <Assets><Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/></Assets>
</PackageManifest>
"""

TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension=".json" ContentType="application/json"/>
  <Default Extension=".js" ContentType="application/javascript"/>
  <Default Extension=".vsixmanifest" ContentType="text/xml"/>
</Types>
"""

with zipfile.ZipFile(HERE / "shoal-terminals.vsix", "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", TYPES)
    z.writestr("extension.vsixmanifest", MANIFEST)
    for name in ("package.json", "extension.js"):
        z.write(HERE / name, f"extension/{name}")
