import contextlib
import io
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from scripts import generate_ios_project
from scripts.check_release import allowed


class IOSProjectTests(unittest.TestCase):
    def test_generated_scheme_loads_and_points_to_native_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for folder in ("LoveShe", "LoveSheTests"):
                (root / folder).mkdir()
                (root / folder / "Fixture.swift").write_text("import Foundation\n")
            with patch.object(generate_ios_project, "ROOT", root), contextlib.redirect_stdout(io.StringIO()):
                generate_ios_project.main()
            project = (root / "LoveShe.xcodeproj" / "project.pbxproj").read_text()
            # XML parsing catches invalid comments that make Xcode discard the test action.
            scheme = ET.parse(root / "LoveShe.xcodeproj" / "xcshareddata" / "xcschemes" / "LoveShe.xcscheme")
            testable = scheme.find("./TestAction/Testables/TestableReference/BuildableReference")
            self.assertIsNotNone(testable)
            self.assertEqual(testable.attrib["BlueprintName"], "LoveSheTests")
            for reference in scheme.findall(".//BuildableReference"):
                self.assertIn(reference.attrib["BlueprintIdentifier"] + " = { isa = PBXNativeTarget;", project)

    def test_ios_release_excludes_signing_and_personal_build_files(self):
        for name in ("ios/distribution.p12", "ios/profile.mobileprovision", "ios/AuthKey.p8",
                     "ios/LoveShe.xcodeproj/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate",
                     "ios/DerivedData/Generated.swift", "ios/LoveShe.ipa"):
            self.assertFalse(allowed(Path(name)), name)
        self.assertTrue(allowed(Path("ios/LoveShe/Database.swift")))
        self.assertTrue(allowed(Path("ios/LoveShe.xcodeproj/project.pbxproj")))


if __name__ == "__main__":
    unittest.main()
