class Mn4Librarian < Formula
  include Language::Python::Virtualenv

  desc "MarginNote4 PDF library filename standardization and tag management"
  homepage "https://github.com/gdtknight/mn4-librarian"
  url "https://github.com/gdtknight/mn4-librarian/archive/refs/tags/v0.1.2.tar.gz"
  sha256 "0c807a68a3ec50eda3955345131189a5c3ac5aaf0b63cd11eaead65f5b04d97e"
  license "MIT"

  depends_on "python@3.12"

  resource "pypdf" do
    url "https://files.pythonhosted.org/packages/03/72/7dfd5ff1c9c37de97a731701f51af091325f123d9d4270361c9c69e4431f/pypdf-6.14.2.tar.gz"
    sha256 "7873f502fe4385e79539b21d872392dc0c4e3714327c15881cbc7fbfd1f95b25"
  end

  def install
    virtualenv_install_with_resources
  end

  def caveats
    <<~EOS
      mn4-librarian은 분류 작업에 claude CLI가 필요합니다:
        https://docs.claude.com/claude-code

      처음 실행하면 라이브러리 폴더/신규 문서 폴더 경로를 물어봅니다:
        mn4-librarian init
    EOS
  end

  test do
    assert_match "mn4-librarian", shell_output("#{bin}/mn4-librarian --version")
  end
end
