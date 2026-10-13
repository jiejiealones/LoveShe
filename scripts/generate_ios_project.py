"""Generate the dependency-free iOS Xcode project from checked-in sources."""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ios"


def identifier(name):
    return hashlib.sha1(name.encode()).hexdigest()[:24].upper()


def quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def main():
    objects = []

    def add(key, isa, properties):
        fields = " ".join(f"{name} = {value};" for name, value in properties.items())
        objects.append(f"\t\t{identifier(key)} = {{ isa = {isa}; {fields} }};")
        return identifier(key)

    def array(items):
        return "(" + ", ".join(items) + ",)" if items else "()"

    source_builds = {}
    groups = []
    for folder in ("LoveShe", "LoveSheTests"):
        references = []
        builds = []
        for source in sorted((ROOT / folder).glob("*.swift")):
            ref = add(f"ref:{folder}/{source.name}", "PBXFileReference", {
                "lastKnownFileType": "sourcecode.swift", "path": quote(source.name), "sourceTree": quote("<group>")})
            references.append(ref)
            builds.append(add(f"build:{folder}/{source.name}", "PBXBuildFile", {"fileRef": ref}))
        if folder == "LoveShe":
            resources = []
            for name, kind in (("PrivacyInfo.xcprivacy", "text.xml"), ("Assets.xcassets", "folder.assetcatalog")):
                ref = add(f"ref:{name}", "PBXFileReference", {"lastKnownFileType": kind, "path": quote(name), "sourceTree": quote("<group>")})
                references.append(ref)
                resources.append(add(f"build:{name}", "PBXBuildFile", {"fileRef": ref}))
            resource_phase = add("resources", "PBXResourcesBuildPhase", {"buildActionMask": "2147483647", "files": array(resources), "runOnlyForDeploymentPostprocessing": "0"})
        groups.append(add(f"group:{folder}", "PBXGroup", {"children": array(references), "path": quote(folder), "sourceTree": quote("<group>")}))
        source_builds[folder] = builds

    products = []
    for name, kind, extension in (("LoveShe", "wrapper.application", "app"), ("LoveSheTests", "wrapper.cfbundle", "xctest")):
        products.append(add(f"product:{name}", "PBXFileReference", {"explicitFileType": kind, "includeInIndex": "0",
                                                                 "path": quote(f"{name}.{extension}"), "sourceTree": "BUILT_PRODUCTS_DIR"}))
    product_group = add("products", "PBXGroup", {"children": array(products), "name": "Products", "sourceTree": quote("<group>")})
    main_group = add("main-group", "PBXGroup", {"children": array(groups + [product_group]), "sourceTree": quote("<group>")})

    configuration_lists = {}
    for scope in ("project", "LoveShe", "LoveSheTests"):
        configurations = []
        for config in ("Debug", "Release"):
            if scope == "project":
                settings = {"CLANG_ENABLE_MODULES": "YES", "IPHONEOS_DEPLOYMENT_TARGET": "17.0", "SDKROOT": "iphoneos",
                            "SWIFT_VERSION": "5.0", "SWIFT_STRICT_CONCURRENCY": "minimal",
                            "SWIFT_OPTIMIZATION_LEVEL": quote("-Onone" if config == "Debug" else "-O"),
                            "DEBUG_INFORMATION_FORMAT": quote("dwarf" if config == "Debug" else "dwarf-with-dsym"),
                            "ENABLE_TESTABILITY": "YES" if config == "Debug" else "NO"}
                if config == "Debug":
                    settings["SWIFT_ACTIVE_COMPILATION_CONDITIONS"] = quote("DEBUG $(inherited)")
            else:
                settings = {"CODE_SIGN_STYLE": "Automatic", "GENERATE_INFOPLIST_FILE": "YES", "CURRENT_PROJECT_VERSION": "1",
                            "MARKETING_VERSION": "0.1.0", "PRODUCT_NAME": quote("$(TARGET_NAME)"),
                            "PRODUCT_BUNDLE_IDENTIFIER": quote("com.example." + scope), "TARGETED_DEVICE_FAMILY": "1",
                            "SUPPORTED_PLATFORMS": quote("iphoneos iphonesimulator")}
                if scope == "LoveShe":
                    settings.update({"INFOPLIST_KEY_CFBundleDisplayName": "LoveShe", "INFOPLIST_KEY_UILaunchScreen_Generation": "YES",  # pragma: allowlist secret -- Xcode setting names
                                     "INFOPLIST_KEY_UIApplicationSceneManifest_Generation": "YES", "INFOPLIST_KEY_LSSupportsOpeningDocumentsInPlace": "YES",  # pragma: allowlist secret -- Xcode setting names
                                     "INFOPLIST_KEY_ITSAppUsesNonExemptEncryption": "NO", "ASSETCATALOG_COMPILER_APPICON_NAME": "AppIcon",  # pragma: allowlist secret -- Xcode setting names
                                     "OTHER_LDFLAGS": quote("$(inherited) -lsqlite3")})
                else:
                    settings.update({"TEST_HOST": quote("$(BUILT_PRODUCTS_DIR)/LoveShe.app/LoveShe"), "BUNDLE_LOADER": quote("$(TEST_HOST)")})
            body = "{ " + " ".join(f"{key} = {value};" for key, value in sorted(settings.items())) + " }"
            configurations.append(add(f"config:{scope}:{config}", "XCBuildConfiguration", {"buildSettings": body, "name": config}))
        configuration_lists[scope] = add(f"configs:{scope}", "XCConfigurationList", {
            "buildConfigurations": array(configurations), "defaultConfigurationIsVisible": "0", "defaultConfigurationName": "Release"})

    proxy = add("test-proxy", "PBXContainerItemProxy", {"containerPortal": identifier("project"), "proxyType": "1",
                                                      "remoteGlobalIDString": identifier("target:LoveShe"), "remoteInfo": "LoveShe"})
    dependency = add("test-dependency", "PBXTargetDependency", {"target": identifier("target:LoveShe"), "targetProxy": proxy})
    targets = []
    for index, name in enumerate(("LoveShe", "LoveSheTests")):
        sources = add(f"sources:{name}", "PBXSourcesBuildPhase", {"buildActionMask": "2147483647", "files": array(source_builds[name]), "runOnlyForDeploymentPostprocessing": "0"})
        frameworks = add(f"frameworks:{name}", "PBXFrameworksBuildPhase", {"buildActionMask": "2147483647", "files": "()", "runOnlyForDeploymentPostprocessing": "0"})
        phases = [sources, frameworks] + ([resource_phase] if name == "LoveShe" else [])
        targets.append(add(f"target:{name}", "PBXNativeTarget", {
            "buildConfigurationList": configuration_lists[name], "buildPhases": array(phases), "buildRules": "()",
            "dependencies": array([dependency] if name == "LoveSheTests" else []), "name": name, "productName": name,
            "productReference": products[index], "productType": quote("com.apple.product-type.application" if index == 0 else "com.apple.product-type.bundle.unit-test")}))
    add("project", "PBXProject", {"attributes": "{ BuildIndependentTargetsInParallel = YES; LastUpgradeCheck = 1600; }",
                                 "buildConfigurationList": configuration_lists["project"], "compatibilityVersion": quote("Xcode 14.0"),
                                 "developmentRegion": "zh_CN", "hasScannedForEncodings": "0", "knownRegions": "(zh_CN, en, Base,)",
                                 "mainGroup": main_group, "productRefGroup": product_group, "projectDirPath": quote(""),
                                 "projectRoot": quote(""), "targets": array(targets)})
    project = ROOT / "LoveShe.xcodeproj"
    project.mkdir(exist_ok=True)
    (project / "project.pbxproj").write_text("// !$*UTF8*$!\n{\n\tarchiveVersion = 1;\n\tclasses = {};\n\tobjectVersion = 56;\n\tobjects = {\n" +
                                          "\n".join(objects) + f"\n\t}};\n\trootObject = {identifier('project')};\n}}\n")
    def reference(name):
        return f'<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{identifier("target:" + name)}" BuildableName="{name}.{"app" if name == "LoveShe" else "xctest"}" BlueprintName="{name}" ReferencedContainer="container:LoveShe.xcodeproj"/>'
    scheme = f'''<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="1600" version="1.3">
 <BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries>
  <BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES">{reference("LoveShe")}</BuildActionEntry>
  <BuildActionEntry buildForTesting="YES" buildForRunning="NO" buildForProfiling="NO" buildForArchiving="NO" buildForAnalyzing="YES">{reference("LoveSheTests")}</BuildActionEntry>
 </BuildActionEntries></BuildAction>
 <TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES"><Testables><TestableReference skipped="NO">{reference("LoveSheTests")}</TestableReference></Testables></TestAction>
 <LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" debugServiceExtension="internal" allowLocationSimulation="YES"><BuildableProductRunnable runnableDebuggingMode="0">{reference("LoveShe")}</BuildableProductRunnable></LaunchAction>
 <ProfileAction buildConfiguration="Release" shouldUseLaunchSchemeArgsEnv="YES" savedToolIdentifier="" useCustomWorkingDirectory="NO" debugDocumentVersioning="YES"><BuildableProductRunnable runnableDebuggingMode="0">{reference("LoveShe")}</BuildableProductRunnable></ProfileAction>
 <AnalyzeAction buildConfiguration="Debug"/>
 <ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/>
</Scheme>
'''
    scheme = "\n".join(line + " <!-- pragma: allowlist secret -- deterministic Xcode IDs -->" if "BlueprintIdentifier" in line else line for line in scheme.splitlines()) + "\n"
    destination = project / "xcshareddata" / "xcschemes"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "LoveShe.xcscheme").write_text(scheme)
    print("Generated ios/LoveShe.xcodeproj and shared LoveShe scheme.")


if __name__ == "__main__":
    main()
